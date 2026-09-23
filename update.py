"""
Rebuilds data.json for the Fixture Run Planner.

Pulls fixtures and difficulty ratings from the Fantasy Premier League API and
expected goals from Understat, rates every team on opponent-adjusted
non-penalty xG, and writes the result.

Runs weekly from GitHub Actions. Nothing is written unless the new data passes
the checks at the bottom, so a bad run leaves the live site on the last good
version rather than breaking it.
"""

import json, math, os, sys, time, gzip, zlib, re, unicodedata, urllib.request, urllib.error, datetime, pathlib

import collections

import minutes as MN

FPL = "https://fantasy.premierleague.com/api"
UND = "https://understat.com"
OUT = pathlib.Path(__file__).parent / "data.json"
OUT_P = pathlib.Path(__file__).parent / "players.json"
OUT_M = pathlib.Path(__file__).parent / "market.json"
# Last season's per-fixture minutes, for the minutes model. Built each
# summer by minutes_prior.py; never edited by hand.
PRIOR_MIN = pathlib.Path(__file__).parent / "minutes_prior.json"

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
# Promoted sides have no record of their own, so their blend constant is a
# judgement rather than a fitted value. Matches the front end.
PROMOTED_K = 4

# Both sources sit behind Cloudflare and refuse traffic that does not look like
# a browser, so present as one and retry a few times before giving up.
UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}


def _decode(body, encoding):
    """Cloudflare compresses replies for anything that looks like a browser."""
    encoding = (encoding or "").lower()
    if encoding == "gzip" or body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    elif encoding == "deflate":
        try:
            body = zlib.decompress(body)
        except zlib.error:
            body = zlib.decompress(body, -zlib.MAX_WBITS)
    return body.decode("utf-8")


def get_json(url, headers=None, tries=4):
    """Fetch and parse JSON, retrying only on errors worth retrying."""
    last = None
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(url, headers={**UA, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(_decode(r.read(), r.headers.get("Content-Encoding")))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.reason}"
        except urllib.error.URLError as e:
            last = f"network: {e.reason}"
        except (UnicodeDecodeError, json.JSONDecodeError, OSError) as e:
            # not transient — retrying will fail the same way
            raise RuntimeError(f"could not read {url} — {type(e).__name__}: {e}")
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


def score_against_market(data, market):
    """
    Put the model's next-gameweek projection beside the market's and score it.

    bias  - do we project more goals than the market, on average
    rmse  - typical distance from it; the number to watch week to week
    r     - do we rank the teams the same way
    slope - regress market on model; ~1 means our spread is right, >1 too flat

    Deliberately read-only: it never touches a rating. Where we disagree is a
    prompt to go and look, not a correction to apply.
    """
    gw = data["nextGw"]
    n_played = data["matchesPlayed"]
    fit = data["fit"]
    b = {}
    for tid, t in data["teams"].items():
        promoted = bool(t.get("promoted"))
        wa = n_played / (n_played + (PROMOTED_K if promoted else fit["kAtk"]))
        wd = n_played / (n_played + (PROMOTED_K if promoted else fit["kDef"]))
        b[tid] = [wa * t["a26"] + (1 - wa) * t["pa"],
                  wd * t["d26"] + (1 - wd) * t["pd"]]
    ma = sum(v[0] for v in b.values()) / len(b)
    md = sum(v[1] for v in b.values()) / len(b)
    atk = {k: v[0] / ma for k, v in b.items()}
    dfn = {k: v[1] / md for k, v in b.items()}
    base = (ma + md) / 2

    model = {}
    for fx in data["fixtures"]:
        if fx[0] != gw:
            continue
        h, a = str(fx[1]), str(fx[2])
        model[data["teams"][h]["short"]] = round(
            base * atk[h] * dfn[a] * fit["home"] * fit["pen"], 3)
        model[data["teams"][a]["short"]] = round(
            base * atk[a] * dfn[h] / fit["home"] * fit["pen"], 3)

    pairs = [(model[k], market[k]) for k in model if k in market]
    card = {"n": len(pairs)}
    if len(pairs) >= 4:
        n = len(pairs)
        errs = [x - y for x, y in pairs]
        mx = sum(x for x, _ in pairs) / n
        my = sum(y for _, y in pairs) / n
        sxy = sum((x - mx) * (y - my) for x, y in pairs)
        sxx = sum((x - mx) ** 2 for x, _ in pairs)
        syy = sum((y - my) ** 2 for _, y in pairs)
        card.update(
            bias=round(sum(errs) / n, 4),
            rmse=round(math.sqrt(sum(e * e for e in errs) / n), 4),
            r=round(sxy / math.sqrt(sxx * syy), 4) if sxx and syy else None,
            slope=round(sxy / sxx, 4) if sxx else None,
        )
    else:
        card.update(bias=None, rmse=None, r=None, slope=None)

    rows = sorted(({"team": k, "model": model[k], "market": market[k],
                    "diff": round(model[k] - market[k], 3)}
                   for k in model if k in market),
                  key=lambda r: -abs(r["diff"]))
    return {"model": model, "scorecard": card, "worst": rows[:5]}


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- players ---

def strip_name(s):
    """Fold accents and punctuation so the two sources' spellings can meet."""
    out = unicodedata.normalize("NFD", s)
    out = "".join(c for c in out if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z ]", "", out).strip()


def match_player(full, web, pool):
    """
    Find an Understat player for an FPL one. Tried in order of confidence:
    exact, one name being a subset of the other, shared surname, web name.
    """
    f, w = strip_name(full), strip_name(web)
    toks = f.split()
    if not toks:
        return None
    for cand in pool:
        if cand["n"] == f:
            return cand
    for cand in pool:
        if cand["n"] and all(t in toks for t in cand["n"].split()):
            return cand
    for cand in pool:
        if all(t in cand["n"].split() for t in toks):
            return cand
    last = toks[-1]
    for cand in pool:
        if cand["n"] == last or cand["n"].endswith(" " + last):
            return cand
    bare = re.sub(r"^[a-z]\.", "", w).strip()
    for cand in pool:
        if bare and bare in cand["n"]:
            return cand
    return None


def build_players(boot, season, short2name, short2id, played):
    """Every FPL player, with non-penalty xG and shots joined from Understat."""
    und_by_team, unmatched = {}, 0
    for sh, tid in short2id.items():
        name = short2name.get(sh)
        if not name:
            continue
        try:
            d = get_json(f"{UND}/getTeamData/{SLUG[name]}/{season}",
                         {"X-Requested-With": "XMLHttpRequest"})
            und_by_team[tid] = [
                {"n": strip_name(p["player_name"]),
                 "npxg": float(p["npxG"]), "xa": float(p["xA"]),
                 "sh": int(p["shots"]), "min": int(p["time"])}
                for p in d["players"]]
        except Exception as e:
            log(f"   no Understat players for {sh}: {e}")
            und_by_team[tid] = []

    def per90(total, minutes):
        return round(total / minutes * 90, 3) if minutes and total is not None else None

    out = []
    for e in boot["elements"]:
        mins = e["minutes"]
        u = match_player(f"{e['first_name']} {e['second_name']}", e["web_name"],
                         und_by_team.get(e["team"], []))
        if u is None and mins > 0:
            unmatched += 1
        npxg = u["npxg"] if u else None
        xa = u["xa"] if u else None
        shots = u["sh"] if u else None
        npxgi = (npxg + xa) if (npxg is not None and xa is not None) else None

        out.append({
            "i": e["id"], "n": e["web_name"], "t": e["team"], "p": e["element_type"],
            "c": e["now_cost"], "own": float(e["selected_by_percent"]),
            "st": e["status"], "news": (e["news"] or "")[:90],
            "cop": e["chance_of_playing_next_round"],
            "min": mins, "starts": e["starts"],
            # Minutes per team game, NOT per start. Dividing by starts hands a
            # player with one start and two cameos 150 min/start, which cannot
            # happen in football. Against matches played it reads as availability
            # and is bounded by 90.
            "mpg": round(mins / played, 1) if played else None,
            "pts": e["total_points"],
            "dc": e["defensive_contribution"],
            "sv": e["saves"], "cs": e["clean_sheets"],
            "gc": e["goals_conceded"],
            "xgc": round(float(e["expected_goals_conceded"]), 2),
            "g": e["goals_scored"], "a": e["assists"],
            "npxg": round(npxg, 2) if npxg is not None else None,
            "xa": round(xa, 2) if xa is not None else None,
            "npxgi": round(npxgi, 2) if npxgi is not None else None,
            "sh": shots,
            "pen": e["penalties_order"],
            "ck": e["corners_and_indirect_freekicks_order"],
            "fk": e["direct_freekicks_order"],
            # rates, all computed the same way so nothing is inconsistent
            "pts90": per90(e["total_points"], mins),
            "dc90": per90(e["defensive_contribution"], mins),
            "sv90": per90(e["saves"], mins),
            "cs90": per90(e["clean_sheets"], mins),
            "gc90": per90(e["goals_conceded"], mins),
            "xgc90": per90(float(e["expected_goals_conceded"]), mins),
            "npxg90": per90(npxg, mins), "xa90": per90(xa, mins),
            "npxgi90": per90(npxgi, mins), "sh90": per90(shots, mins),
        })
    log(f"players: {len(out)} built, {unmatched} with minutes had no Understat row")
    return out


# ---------------------------------------------------------------- minutes ---
MINUTES_HORIZON = 6      # gameweeks of expected minutes written per player


def shown(p):
    """
    A probability as published: two places, and never 1.00 -- in the
    backtest the few players rated 99.5%+ started about 96% of the time.
    """
    return min(round(p, 2), 0.99)


def build_minutes(boot, raw_fixtures, next_gw, season, today=None, fetch=None):
    """
    The minutes model (minutes.py; claude/backtest-minutes.md) for every
    player: expected minutes in each of the next MINUTES_HORIZON gameweeks,
    and the chance of starting, of playing 60+ and of appearing at all in
    his next fixture.

    Reads one event/{gw}/live/ reply per gameweek played so far -- each lists
    every registered player with his minutes in every team fixture, a
    0-minute entry included when he did not play -- plus last season from
    minutes_prior.json. FPL's injury news is applied on top, then each side's
    line-up is filled back up to its usual shape.

    Returns ({player id: fields}, summary). Raises if the rows look wrong, so
    the caller publishes the players without these fields rather than
    publishing something wrong.
    """
    fetch = fetch or get_json
    today = today or datetime.datetime.now(datetime.timezone.utc).date()

    finished = {f["id"]: (f["event"], f.get("kickoff_time") or "")
                for f in raw_fixtures
                if f.get("event") and (f.get("finished") or f.get("finished_provisional"))}
    lives = {gw: fetch(f"{FPL}/event/{gw}/live/")
             for gw in sorted({g for g, _ in finished.values()})}
    frows = MN.live_fixture_rows(lives, finished)
    rows = MN.live_rows(frows)

    # The rows must add up. Each gameweek, FPL's starts total exactly 22 per
    # fixture -- exact even in a double gameweek, where the split between the
    # two games is a guess (see live_fixture_rows), so the exact test is per
    # gameweek and the per-fixture one is loose. It is skipped for a gameweek
    # still being played: FPL's starts then include the game in progress. A
    # player FPL has since deleted could leave a gameweek a start or two
    # short, which is harmless. Anything else means the replies were read
    # wrongly. A finished fixture with no rows at all is FPL not having caught
    # up yet: skipped, and picked up on the next run.
    rows_fx = collections.Counter(f for _, f, _, _, _ in frows)
    per_fx = {f: 0 for f in rows_fx}
    for _, f, _, st, _ in frows:
        per_fx[f] += st
    live_gws = {f["event"] for f in raw_fixtures
                if f.get("event") and f["id"] not in finished}
    per_gw, fx_in_gw = collections.Counter(), collections.Counter()
    for f, n in per_fx.items():
        per_gw[finished[f][0]] += n
        fx_in_gw[finished[f][0]] += 1
    short = {gw: 22 * fx_in_gw[gw] - n for gw, n in per_gw.items()
             if gw not in live_gws and n != 22 * fx_in_gw[gw]}
    loose = {f: n for f, n in per_fx.items() if not 19 <= n <= 25}
    thin = {f: n for f, n in rows_fx.items() if n < 40}
    if any(v < 0 or v > 2 for v in short.values()) or loose or thin:
        raise ValueError(f"FPL's live rows do not add up -- starters short per gameweek {short}, "
                         f"odd fixtures {dict(list(loose.items())[:4])}, "
                         f"fixtures with under 40 players listed {dict(list(thin.items())[:4])}")
    if short:
        log(f"   minutes: gameweek(s) {short} a start or two short -- carrying on")
    missing = sorted(set(finished) - set(rows_fx))
    if missing:
        log(f"   minutes: {len(missing)} finished fixture(s) not in FPL's live data yet: {missing[:6]}")

    want = f"{season - 1}/{str(season)[2:]}"
    prior = {}
    if PRIOR_MIN.exists():
        pf = json.loads(PRIOR_MIN.read_text())
        if pf.get("season") == want:
            prior = pf["players"]
        else:
            log(f"!! minutes_prior.json is for {pf.get('season')}, not {want} -- "
                f"run `python minutes_prior.py {want.replace('/', '-')}` once the "
                f"archive has it. Using no prior season.")
    else:
        log("!! minutes_prior.json missing -- using no prior season")

    players = [e for e in boot["elements"] if e["element_type"] in MN.FPL_POS]
    pos_of = {e["id"]: MN.FPL_POS[e["element_type"]] for e in players}

    horizon = list(range(next_gw, min(38, next_gw + MINUTES_HORIZON - 1) + 1))
    fixtures = sorted((f for f in raw_fixtures if f.get("event") in horizon),
                      key=lambda f: (f["event"], f.get("kickoff_time") or "", f["id"]))

    # forecast every player for every fixture his side plays in the window,
    # then fill each side's line-up: one keeper, ten outfield players
    mem = {e["id"]: MN.fold(rows.get(e["id"], []),
                            MN.decode_prior(prior.get(str(e["code"]), [])))
           for e in players}
    by_team = collections.defaultdict(list)
    for e in players:
        by_team[e["team"]].append(e)
    fc, raw_sum = {}, {}
    for f in fixtures:
        gw = f["event"]; h = horizon.index(gw) + 1
        k = f.get("kickoff_time")
        day = datetime.date.fromisoformat(k[:10]) if k else today
        for t in (f["team_h"], f["team_a"]):
            groups = collections.defaultdict(list)
            for e in by_team[t]:
                av = MN.availability(e["status"], e["chance_of_playing_next_round"],
                                     e["news"], day, today, gw == next_gw)
                o = MN.forecast(mem[e["id"]], pos_of[e["id"]], e["now_cost"] / 10, h, av)
                groups[MN.group(pos_of[e["id"]])].append((e["id"], o, av))
            if gw == next_gw:
                raw_sum[(t, f["id"])] = sum(o["start"] for g in groups.values() for _, o, _ in g)
            for g, members in groups.items():
                filled = MN.fill_lineup([o for _, o, _ in members], MN.LINEUP[g],
                                        [a for _, _, a in members])
                for (pid, _, _), o in zip(members, filled):
                    fc[(pid, f["id"])] = (gw, o)

    out, filled_sum = {}, collections.Counter()
    for e in players:
        pid = e["id"]; xm = [0.0] * len(horizon); first = None
        for f in fixtures:
            if (pid, f["id"]) in fc:
                gw, o = fc[(pid, f["id"])]
                xm[horizon.index(gw)] += o["xmin"]
                if gw == next_gw and first is None:
                    first = o
        rec = {"xm": [round(x) for x in xm]}
        if first is not None:
            rec.update(ps=shown(first["start"]), p60=shown(first["p60"]), pa=shown(first["app"]))
            filled_sum[e["team"]] += first["start"]      # a double counts its first game
        out[pid] = rec

    # Before the fill, a side's chances of starting in a fixture should
    # already be roughly eleven -- a little under when injury news has taken
    # players out. Far outside that, the rows or the news were read wrongly.
    if not raw_sum:
        raise ValueError(f"no player has a fixture in GW{next_gw}")
    bad = {k: round(v, 1) for k, v in raw_sum.items() if not 7.0 <= v <= 14.0}
    if bad:
        raise ValueError(f"expected starters per side and fixture out of range before the fill: {bad}")
    n_rows = sum(len(v) for v in rows.values())
    log(f"minutes: {len(out)} players, {n_rows} player-fixtures this season "
        f"({len(lives)} gameweeks) + {len(prior)} players carried from "
        f"{want if prior else 'nowhere'}; starters per side before the line-up "
        f"fill {min(raw_sum.values()):.1f}-{max(raw_sum.values()):.1f}, after "
        f"{min(filled_sum.values()):.1f}-{max(filled_sum.values()):.1f}")
    summary = {"gws": horizon, "priorSeason": want if prior else None,
               "built": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "about": "xm: expected minutes in each gameweek listed in gws (0 = no fixture). "
                        "ps / p60 / pa: chance of starting, of playing 60+ and of appearing "
                        "at all in the next fixture. See minutes.py."}
    return out, summary


MINUTES_KEEP_HOURS = 72


def keep_minutes(path, players, pdata, next_gw, now=None):
    """
    When the minutes model fails, carry the last published figures over --
    but only if they are from this season, cover exactly the same gameweeks,
    and are under MINUTES_KEEP_HOURS old. Anything else is dropped rather
    than shown for the wrong weeks or with news days out of date. Returns
    how many players kept figures.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        old = json.loads(pathlib.Path(path).read_text())
        mm = old.get("minutes") or {}
        built = datetime.datetime.fromisoformat(mm.get("built") or old["generated"])
        if (old.get("season") != pdata["season"] or (mm.get("gws") or [None])[0] != next_gw
                or now - built > datetime.timedelta(hours=MINUTES_KEEP_HOURS)):
            return 0
        prev = {q["i"]: q for q in old["players"]}
        kept = 0
        for p in players:
            q = prev.get(p["i"], {})
            if "xm" in q:
                p.update({k: q[k] for k in ("xm", "ps", "p60", "pa") if k in q})
                kept += 1
        if kept:
            pdata["minutes"] = dict(mm, stale=True, built=mm.get("built") or old["generated"])
        return kept
    except Exception:
        return 0


def derive_next_gw(events, now=None):
    """
    The gameweek the site should point at: the next one you can still transfer
    into.  Everything on the front end hangs off this, so it has to roll over
    the moment a deadline passes.

    Do NOT use ``event["finished"]``.  FPL only sets it once bonus points are
    confirmed and the round is data-checked, which lags the last kick-off by a
    day or more -- and stays False indefinitely if a fixture is postponed.  On
    7 Sep 2026 all ten GW3 matches were finished and ``finished`` was still
    False, which pinned the whole site to GW3.

    In order of trust:
      1. ``is_next`` -- FPL's own answer, flipped at each deadline.
      2. The first deadline still in the future.  Deterministic, and right even
         when the flags are mid-update.
      3. The first round not marked finished (the old behaviour, last resort).
      4. 38.
    """
    if not events:
        return 38
    now = now or datetime.datetime.now(datetime.timezone.utc)

    nxt = next((e["id"] for e in events if e.get("is_next")), None)
    if nxt:
        return nxt

    for e in events:
        raw = e.get("deadline_time")
        if not raw:
            continue
        when = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        if when > now:
            return e["id"]

    # Past the last deadline of the season, or the calendar is unusable.
    cur = next((e["id"] for e in events if e.get("is_current")), None)
    if cur:
        return min(38, cur + 1) if cur < 38 else 38
    return next((e["id"] for e in events if not e.get("finished")), 38)


def next_gw_from_deadlines(deadlines, today=None):
    """
    Fallback for when FPL is unreachable and all we have is the date-only
    deadline list from the previous data.json.  A deadline dated today has not
    necessarily passed, so today still counts as upcoming.
    """
    today = today or datetime.date.today().isoformat()
    return next((i + 1 for i, d in enumerate(deadlines) if d[:10] >= today), 38)


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
            "live": True, "boot": boot, "rawFixtures": raw,
            "short2id": {t["short_name"]: t["id"] for t in boot["teams"]},
            "meta": {t["id"]: {"name": t["name"],
                               "short": t["short_name"].lower()}
                     for t in boot["teams"]},
            "deadlines": [e["deadline_time"][:10] for e in boot["events"]],
            "deadlineTimes": [e["deadline_time"] for e in boot["events"]],
            "nextGw": derive_next_gw(boot["events"]),
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
            "live": False, "boot": None, "rawFixtures": None,
            "short2id": {v["short"].upper(): k for k, v in meta.items()},
            "meta": meta,
            "deadlines": previous["deadlines"],
            "deadlineTimes": previous.get("deadlineTimes", []),
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
        next_gw = next_gw_from_deadlines(deadlines)
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
        "deadlineTimes": fpl["deadlineTimes"] or deadlines,
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
    if not (1 <= data["nextGw"] <= 38):
        problems.append(f"nextGw={data['nextGw']} is outside 1-38")
    # A gameweek whose own deadline has been and gone cannot be the next one.
    # This is the check that would have caught the GW3 stall on 7 Sep 2026:
    # GW3's deadline was the 4th, and nextGw was still 3.  Rescheduled matches
    # can put a team's played count ahead of the round number, so the calendar
    # is the thing to test against, not matchesPlayed.
    earliest = next_gw_from_deadlines(data["deadlines"])
    if data["nextGw"] < earliest:
        problems.append(
            f"nextGw={data['nextGw']} but its deadline "
            f"({data['deadlines'][data['nextGw'] - 1]}) has passed — "
            f"the calendar says GW{earliest}")
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

    # players are a separate file so the ticker never waits on them
    try:
        if fpl["boot"] is None:
            raise RuntimeError("FPL was unavailable, so there is no player data to build from")
        players = build_players(fpl["boot"], season, short2name, short2id, played)
        if len(players) < 300:
            raise ValueError(f"only {len(players)} players")
        pdata = {"generated": data["generated"], "season": data["season"],
                 "matchesPlayed": played, "nextGw": next_gw,
                 "teams": {k: {"name": v["name"], "short": v["short"]}
                           for k, v in data["teams"].items()},
                 "players": players}
        # The minutes model goes in its own try: if it fails, the players
        # still publish. Yesterday's minutes are kept when they still cover
        # the same gameweeks -- a single failed fetch should not blank them --
        # and dropped once they would describe the wrong ones.
        try:
            mins, summary = build_minutes(fpl["boot"], fpl["rawFixtures"], next_gw, season)
            for p in players:
                p.update(mins.get(p["i"], {}))
            pdata["minutes"] = summary
        except Exception as e:
            kept = keep_minutes(OUT_P, players, pdata, next_gw)
            log(f"!! minutes model not run ({e}) -- "
                + (f"kept the last run's figures for {kept} players" if kept
                   else "players published without it"))
        OUT_P.write_text(json.dumps(pdata, separators=(",", ":")))
        log(f"wrote {OUT_P.name}: {len(players)} players")
    except Exception as e:
        log(f"!! players not rebuilt ({e}) — keeping the previous players.json")
    # --- bookmaker odds ----------------------------------------------------
    # A reference forecast, fetched the same way the players are: in its own
    # try, so a third-party outage or an exhausted quota never stops the site
    # rebuilding. Nothing here feeds the model -- it is the scorecard the model
    # is measured against (claude/backtest-odds-benchmark.md).
    try:
        if not os.environ.get("ODDS_API_KEY"):
            raise RuntimeError("ODDS_API_KEY is not set")
        import odds as odds_mod
        priced, remaining = odds_mod.priced_fixtures()
        if not priced:
            raise ValueError("the odds feed returned no priced fixtures")

        # Join each priced fixture to its gameweek through data.json's own
        # fixture list, keyed on (home id, away id) -- unique within a season.
        # Joining on the fixture rather than the date means a rearranged game
        # lands in the right gameweek, which a date lookup would get wrong.
        id_by_short = {t["short"]: tid for tid, t in teams.items()}
        pair_gw = {(str(f[1]), str(f[2])): f[0] for f in fixtures}
        by_gw, unjoined = {}, []
        for pf in priced:
            h, a = id_by_short.get(pf["home"]), id_by_short.get(pf["away"])
            gw = pair_gw.get((h, a)) if h and a else None
            if gw is None:
                unjoined.append(f"{pf['home']}-{pf['away']}")
                continue
            slot = by_gw.setdefault(str(gw), {})
            slot[pf["home"]] = round(pf["home_xg"], 3)
            slot[pf["away"]] = round(pf["away_xg"], 3)
        if unjoined:
            log(f"   odds: {len(unjoined)} priced fixtures matched no scheduled "
                f"game and were dropped ({', '.join(unjoined[:5])})")
        if not by_gw:
            raise ValueError("no priced fixture could be joined to a gameweek")
        market = by_gw.get(str(next_gw), {})

        # score the model against it, so every build leaves a record
        scored = score_against_market(data, market)
        OUT_M.write_text(json.dumps({
            "generated": data["generated"],
            "nextGw": next_gw,
            "creditsRemaining": remaining,
            "gw": by_gw,                  # every priced gameweek, for the ticker
            "market": market,             # the next one alone, for the scorecard
            "model": scored["model"],
            "scorecard": scored["scorecard"],
        }, separators=(",", ":")))
        sc = scored["scorecard"]
        card = ("not enough priced fixtures to score" if sc["rmse"] is None else
                f"bias {sc['bias']:+.3f}, RMSE {sc['rmse']:.3f}, "
                f"r {sc['r']:.3f}, slope {sc['slope']:.3f}")
        log(f"wrote {OUT_M.name}: GW{'/GW'.join(sorted(by_gw, key=int))} priced "
            f"({sum(len(v) for v in by_gw.values())} team-fixtures), {card} "
            f"({remaining} credits left)")
        for row in scored["worst"]:
            log(f"    {row['team'].upper():5} model {row['model']:.2f}  "
                f"market {row['market']:.2f}  {row['diff']:+.2f}")
    except Exception as e:
        log(f"!! odds not fetched ({e}) - keeping the previous market.json")

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
