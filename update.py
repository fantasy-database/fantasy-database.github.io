"""
Rebuilds data.json for the Fixture Run Planner.

Pulls fixtures and difficulty ratings from the Fantasy Premier League API and
expected goals from Understat, rates every team on opponent-adjusted
non-penalty xG, and writes the result.

Runs weekly from GitHub Actions. Nothing is written unless the new data passes
the checks at the bottom, so a bad run leaves the live site on the last good
version rather than breaking it.
"""

import json, math, sys, time, urllib.request, urllib.error, datetime, pathlib

FPL = "https://fantasy.premierleague.com/api"
UND = "https://understat.com"
OUT = pathlib.Path(__file__).parent / "data.json"

# Understat's full team names -> FPL's short codes. Covers every side to appear
# in the Premier League recently, so promotion and relegation need no edits.
NAME2SHORT = {
    "Arsenal": "ARS", "Aston Villa": "AVL", "Bournemouth": "BOU", "Brentford": "BRE",
    "Brighton": "BHA", "Burnley": "BUR", "Chelsea": "CHE", "Coventry": "COV",
    "Crystal Palace": "CRY", "Everton": "EVE", "Fulham": "FUL", "Hull": "HUL",
    "Ipswich": "IPS", "Leeds": "LEE", "Leicester": "LEI", "Liverpool": "LIV",
    "Luton": "LUT", "Manchester City": "MCI", "Manchester United": "MUN",
    "Newcastle United": "NEW", "Norwich": "NOR", "Nottingham Forest": "NFO",
    "Sheffield United": "SHU", "Southampton": "SOU", "Sunderland": "SUN",
    "Tottenham": "TOT", "Watford": "WAT", "West Ham": "WHU",
    "Wolverhampton Wanderers": "WOL",
}
SLUG = {n: n.replace(" ", "_") for n in NAME2SHORT}

# Averaged over the fifteen sides promoted in the five seasons to 2025/26,
# in their first year up. Non-penalty xG and xGA per game.
PROMOTED_PRIOR = {"a": 1.1145, "d": 1.9492}

# Both sources sit behind Cloudflare and refuse traffic that does not look like
# a browser, so present as one and retry a few times before giving up.
UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-GB,en;q=0.9",
}


def get_json(url, headers=None, tries=4):
    last = None
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(url, headers={**UA, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.reason}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if attempt < tries:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"could not fetch {url} after {tries} tries — {last}")


def understat_season(today=None):
    """Understat labels a season by the calendar year it starts in."""
    d = today or datetime.date.today()
    return d.year if d.month >= 7 else d.year - 1


def team_matches(slug, season):
    """Every completed match for one team: (opponent short, home?, xG, xGA)."""
    d = get_json(f"{UND}/getTeamData/{slug}/{season}",
                 {"X-Requested-With": "XMLHttpRequest"})
    out = []
    for m in d["dates"]:
        if not m["isResult"]:
            continue
        home = m["side"] == "h"
        opp = m["a" if home else "h"]["title"]
        if opp not in NAME2SHORT:
            raise KeyError(f"unmapped opponent {opp!r} — add it to NAME2SHORT")
        out.append((NAME2SHORT[opp], home,
                    float(m["xG"]["h" if home else "a"]),
                    float(m["xG"]["a" if home else "h"])))
    return out, d


def np_share(payload):
    """What fraction of a team's xG and xGA was not from penalties."""
    sit = payload["statistics"]["situation"]
    xg = sum(v["xG"] for v in sit.values())
    xga = sum(v["against"]["xG"] for v in sit.values())
    pen = sit.get("Penalty", {"xG": 0, "against": {"xG": 0}})
    a = (xg - pen["xG"]) / xg if xg else 1.0
    d = (xga - pen["against"]["xG"]) / xga if xga else 1.0
    return a, d


def solve(matches, iters=60):
    """
    Attack and defence ratings that explain the season's xG, adjusting each
    match for the opponent faced and the venue. Iterated to convergence.
    """
    teams = list(matches)
    hx = ax = hn = an = 0
    for t in teams:
        for _, home, f, _ in matches[t]:
            if home: hx, hn = hx + f, hn + 1
            else:    ax, an = ax + f, an + 1
    hfac = math.sqrt((hx / hn) / (ax / an))
    flat = [m[2] for t in teams for m in matches[t]]
    base = sum(flat) / len(flat)

    A = {t: 1.0 for t in teams}
    D = {t: 1.0 for t in teams}
    for _ in range(iters):
        nA, nD = {}, {}
        for t in teams:
            sf = ef = sa = ea = 0.0
            for opp, home, f, a in matches[t]:
                if opp not in D:
                    continue
                m = hfac if home else 1 / hfac
                sf += f; ef += base * D[opp] * m
                sa += a; ea += base * A[opp] / m
            nA[t] = sf / ef if ef else 1.0
            nD[t] = sa / ea if ea else 1.0
        ma = sum(nA.values()) / len(nA)
        md = sum(nD.values()) / len(nD)
        A = {t: nA[t] / ma for t in teams}
        D = {t: nD[t] / md for t in teams}
    return A, D, base, hfac


def corr(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = sum((a - mx) ** 2 for a in x)
    sy = sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sx * sy) if sx and sy else 0.0


def fit_k(matches, A, D, base, hfac):
    """
    Split each team's season into odd and even matches, rate both halves, and
    see how well a team predicts itself. k = n(1-r)/r turns that reliability
    into a blend constant. Attack and defence are not equally repeatable, so
    they get their own.
    """
    halves = {0: ([], []), 1: ([], [])}
    for t in matches:
        for par in (0, 1):
            ms = matches[t][par::2]
            sf = ef = sa = ea = 0.0
            for opp, home, f, a in ms:
                m = hfac if home else 1 / hfac
                sf += f; ef += base * D.get(opp, 1) * m
                sa += a; ea += base * A.get(opp, 1) / m
            halves[par][0].append(sf / ef if ef else 1)
            halves[par][1].append(sa / ea if ea else 1)
    n = len(matches[next(iter(matches))]) / 2
    out = {}
    for i, key in enumerate(("kAtk", "kDef")):
        r = corr(halves[0][i], halves[1][i])
        out[key] = max(0, round(n * (1 - r) / r)) if r > 0 else 40
    return out


def log(msg):
    print(msg, flush=True)


def load_fpl(previous):
    """
    Fixtures, deadlines and difficulty ratings from the FPL API.

    The API refuses datacentre traffic often enough that this cannot be a hard
    dependency. When it will not answer we reuse whatever the last good
    data.json held — fixtures barely move, and the weekly change we actually
    care about is the expected goals, which come from Understat.
    """
    try:
        log("fetching FPL bootstrap…")
        boot = get_json(f"{FPL}/bootstrap-static/")
        log("fetching FPL fixtures…")
        raw = get_json(f"{FPL}/fixtures/")
        return {
            "live": True,
            "short2id": {t["short_name"]: t["id"] for t in boot["teams"]},
            "meta": {t["id"]: {"name": t["name"],
                               "short": t["short_name"].lower()}
                     for t in boot["teams"]},
            "deadlines": [e["deadline_time"][:10] for e in boot["events"]],
            "nextGw": next((e["id"] for e in boot["events"]
                            if not e["finished"]), 38),
            "fixtures": [[f["event"], f["team_h"], f["team_a"],
                          f["team_h_difficulty"], f["team_a_difficulty"]]
                         for f in raw if f["event"]],
        }
    except Exception as e:
        if not previous:
            raise RuntimeError(
                f"FPL unreachable and no previous data.json to fall back on — {e}")
        log(f"!! FPL unreachable ({e})")
        log("   falling back to the fixtures and deadlines already in data.json")
        meta = {int(k): {"name": v["name"], "short": v["short"]}
                for k, v in previous["teams"].items()}
        return {
            "live": False,
            "short2id": {v["short"].upper(): k for k, v in meta.items()},
            "meta": meta,
            "deadlines": previous["deadlines"],
            "nextGw": None,          # worked out from the calendar below
            "fixtures": previous["fixtures"],
        }


def main():
    previous = json.loads(OUT.read_text()) if OUT.exists() else None
    fpl = load_fpl(previous)
    short2id = fpl["short2id"]
    teams_meta = fpl["meta"]
    deadlines = fpl["deadlines"]
    fixtures = fpl["fixtures"]

    season = understat_season()
    prev = season - 1

    # last season: everyone who was in the division
    anchor, _ = team_matches("Arsenal", prev)
    prev_shorts = sorted({opp for opp, *_ in anchor} | {"ARS"})
    short2name = {v: k for k, v in NAME2SHORT.items()}

    prev_matches, prev_share = {}, {}
    for sh in prev_shorts:
        ms, payload = team_matches(SLUG[short2name[sh]], prev)
        prev_share[sh] = np_share(payload)
        prev_matches[sh] = [(o, h, f * prev_share[sh][0], a * prev_share[sh][1])
                            for o, h, f, a in ms]

    log(f"last season: {len(prev_matches)} teams pulled")
    A, D, base, hfac = solve(prev_matches)
    fit = fit_k(prev_matches, A, D, base, hfac)

    # this season so far
    cur_shorts = sorted(short2id)
    cur_matches = {}
    for sh in cur_shorts:
        name = short2name.get(sh)
        if name is None:
            raise KeyError(f"no Understat name for FPL side {sh!r}")
        ms, payload = team_matches(SLUG[name], season)
        sa, sd = np_share(payload)
        cur_matches[sh] = [(o, h, f * sa, a * sd) for o, h, f, a in ms]

    log(f"this season: {len(cur_matches)} teams pulled")
    played = max((len(v) for v in cur_matches.values()), default=0)
    next_gw = fpl["nextGw"]
    if next_gw is None:
        today = datetime.date.today().isoformat()
        next_gw = next((i + 1 for i, d in enumerate(deadlines) if d >= today), 38)
    log(f"{played} matches played, next GW{next_gw}"
        + ("" if fpl["live"] else "  (derived — FPL was unavailable)"))

    promoted = [sh for sh in cur_shorts if sh not in prev_matches]
    pa_rat = PROMOTED_PRIOR["a"] / base
    pd_rat = PROMOTED_PRIOR["d"] / base
    Aof = lambda t: pa_rat if t in promoted else A.get(t, 1.0)
    Dof = lambda t: pd_rat if t in promoted else D.get(t, 1.0)

    teams = {}
    for sh in cur_shorts:
        tid = short2id[sh]
        rec = dict(teams_meta[tid])
        if sh in promoted:
            rec["pa"], rec["pd"] = PROMOTED_PRIOR["a"], PROMOTED_PRIOR["d"]
            rec["promoted"] = 1
        else:
            rec["pa"] = round(A[sh] * base, 4)
            rec["pd"] = round(D[sh] * base, 4)

        sf = ef = sa = ea = 0.0
        for opp, home, f, a in cur_matches[sh]:
            m = hfac if home else 1 / hfac
            sf += f; ef += base * Dof(opp) * m
            sa += a; ea += base * Aof(opp) / m
        rec["a26"] = round(sf / ef * base, 4) if ef else rec["pa"]
        rec["d26"] = round(sa / ea * base, 4) if ea else rec["pd"]
        teams[str(tid)] = rec

    # penalties are stripped from the ratings; restore them when projecting goals
    tot = sum(m[2] for t in prev_matches for m in
              [(o, h, f / prev_share[t][0], a) for o, h, f, a in prev_matches[t]])
    npt = sum(m[2] for t in prev_matches for m in prev_matches[t])
    pen = round(tot / npt, 4) if npt else 1.0

    data = {
        "generated": datetime.datetime.now(datetime.timezone.utc)
                      .isoformat(timespec="seconds"),
        "season": f"{season}/{str(season + 1)[2:]}",
        "matchesPlayed": played,
        "nextGw": next_gw,
        "fit": {"kAtk": fit["kAtk"], "kDef": fit["kDef"],
                "home": round(hfac, 3), "pen": pen},
        "promotedPrior": PROMOTED_PRIOR,
        "teams": teams,
        "deadlines": deadlines,
        "fixtures": fixtures,
        "fplLive": fpl["live"],
    }

    # --- refuse to publish anything that looks wrong -----------------------
    problems = []
    if len(data["teams"]) != 20:
        problems.append(f"{len(data['teams'])} teams, expected 20")
    if len(data["fixtures"]) < 300:
        problems.append(f"only {len(data['fixtures'])} fixtures")
    if len(data["deadlines"]) != 38:
        problems.append(f"{len(data['deadlines'])} deadlines, expected 38")
    for tid, t in data["teams"].items():
        for key in ("pa", "pd", "a26", "d26"):
            v = t.get(key)
            if v is None or not (0.05 < v < 6):
                problems.append(f"{t['name']} {key}={v}")
    if not (1.0 <= data["fit"]["home"] <= 1.4):
        problems.append(f"home factor {data['fit']['home']}")
    if problems:
        print("REFUSING TO WRITE — data failed validation:", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        sys.exit(1)

    OUT.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {OUT.name}: {len(data['teams'])} teams, "
          f"{data['matchesPlayed']} matches played, next GW{data['nextGw']}, "
          f"kAtk={data['fit']['kAtk']} kDef={data['fit']['kDef']} "
          f"home={data['fit']['home']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        raise
