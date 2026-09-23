"""
Expected points: what a player should score in a fixture, in FPL points.

Pure functions, no network, no dependencies. update.py feeds it FPL's
per-gameweek stats; the backtest (claude/backtest-points.md) feeds it the same
thing from past seasons.

For one fixture:

    appearance   P(appears) + P(plays 60+)                     [minutes.py]
    goals        his share of his side's xG x the side's expected goals
                 in this fixture x (expected minutes / 90) x points per goal
    assists      the same with his share of expected assists, x 3
    clean sheet  P(60+) x P(side keeps a clean sheet) x points for his position
    conceded     GK/DEF: -1 per 2 goals conceded while he plays
    saves        GK: 1 per 3 saves; his saves per unit of the opponent's xG
    DefCon       2 x P(60+) x how often he reaches the threshold in a 60+ game
    cards        his yellow and red rates per 90 x expected minutes
    bonus        a fitted line: how much bonus each goal, assist, clean sheet
                 and 60-minute game has been worth at his position

A player's rates are his own decayed record shrunk towards a prior, the same
way everything else on the site is: a share of his side's xG over two
matches is noise. The attacking prior depends on price and position, because
price is the best guess there is about a player with no record.

The side's expected goals come from the fixture model (model.js, or the
market price where there is one), and a clean sheet is exp(-expected goals
against), exactly as on the ticker -- so the projections cannot disagree with
the fixture colours.
"""

import collections, math

# ---- scoring ---------------------------------------------------------------
GOAL = {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4}
CLEAN = {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0}
DEFCON = {"GK": None, "DEF": 10, "MID": 12, "FWD": 12}   # threshold, 2 points
POSITIONS = ("GK", "DEF", "MID", "FWD")

# ---- fitted constants (claude/backtest-points.md) --------------------------
# Tuned on 2024/25 (2023/24 as its prior season), scored untouched on 2025/26.
LAM_P = 0.94        # weight of each older appearance relative to the next
DELTA_P = 0.25      # extra discount on last season's record
K_G = 4.0           # shrinkage of goal share, in games of his side's xG
K_A = 2.0           # shrinkage of assist share
K_SV = 24.0         # shrinkage of the save rate (GK), in games of opponent xG
K_DC = 4.0          # shrinkage of the DefCon hit rate, in 60-minute games (not tunable
                    # on 2024/25, which scored no DefCon; a judgement)
K_Y = 20.0          # shrinkage of the card rates, in 90s

# Priors and calibrations: plain descriptions of a season, refitted each summer
# on the season just finished (fit_priors in the backtest harness) -- here
# 2025/26, the same "fit on last season, forecast this one" arrangement the
# backtest scored.
# Share of the side's xG per 90 on the pitch: exp(a + b * price) by position.
SHARE_G = {"GK": (-12.8745, 0.8197), "DEF": (-3.7047, 0.106), "MID": (-3.2104, 0.1598), "FWD": (-1.4041, 0.026)}
SHARE_A = {"GK": (-8.5214, 0.3842), "DEF": (-3.3395, 0.0327), "MID": (-2.9955, 0.0947), "FWD": (-3.0166, -0.0169)}
SAVE_RATE = 1.979                 # GK saves per goal of opponent xG
DC_RATE = {"GK": 0.0, "DEF": 0.2697, "MID": 0.1792, "FWD": 0.0118}   # hit rate in 60+ games
YC90 = {"GK": 0.07237, "DEF": 0.1815, "MID": 0.18771, "FWD": 0.14859}
RC90 = {"GK": 0.00132, "DEF": 0.00821, "MID": 0.00479, "FWD": 0.0}
MISC90 = {"GK": 0.06185, "DEF": -0.01768, "MID": -0.00733, "FWD": -0.02377}   # pens saved/missed, own goals
ASSIST_CAL = 1.3788                 # FPL assists per expected assist
# bonus per: (60+ game, goal, assist, clean sheet, 3 saves, DefCon), least squares
BONUS = {"GK": (-0.1911, 0.0, 0.3788, 0.7833, 0.3436, 0.0), "DEF": (-0.0732, 0.9929, 0.4899, 0.6199, 0.0, 0.19), "MID": (0.0352, 1.3539, 0.522, -0.0429, 0.0, 0.1804), "FWD": (0.0266, 1.3567, 0.2524, 0.0155, 0.0, 0.1217)}

FPL_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def _p(p, name):
    """A constant, overridable by the backtest; update.py never overrides."""
    return p[name] if p and name in p else globals()[name]


# ---- a player's record -----------------------------------------------------
KEYS = ("X", "A", "E", "SV", "O", "D60", "N60", "YC", "RC", "M90", "MISC", "DCN")


def empty():
    return {k: 0.0 for k in KEYS}


def step(st, row, lam):
    """
    Fold in one appearance. row: m (minutes), xg, xa, tf (his side's xG in
    that match), ta (the opponent's xG), sv, dc (defensive contributions,
    None if the season did not count them), yc, rc, misc (points from
    penalties saved/missed and own goals), pos.
    Games he did not play in are skipped: they are the minutes model's business.
    """
    m = row["m"]
    if m <= 0:
        return st
    f = m / 90.0
    out = {k: lam * v for k, v in st.items()}
    out["X"] += row["xg"]; out["A"] += row["xa"]
    out["E"] += row["tf"] * f; out["O"] += row["ta"] * f
    out["SV"] += row["sv"]
    out["YC"] += row["yc"]; out["RC"] += row["rc"]; out["M90"] += f
    out["MISC"] += row.get("misc", 0.0)
    if m >= 60 and row.get("dc") is not None:
        thr = DEFCON.get(row["pos"])
        out["N60"] += 1
        out["D60"] += 1.0 if thr and row["dc"] >= thr else 0.0
    return out


def carry(st, delta):
    return {k: v * delta for k, v in st.items()}


def record(rows, lam=None, prior_rows=(), delta=None, p=None, prior_state=None):
    """
    A player's record: last season (as rows, or as the stored end-of-season
    state in points_prior.json) discounted by DELTA_P, then this season.
    """
    lam = _p(p, "LAM_P") if lam is None else lam
    delta = _p(p, "DELTA_P") if delta is None else delta
    st = empty()
    for r in prior_rows:
        st = step(st, r, lam)
    if prior_state:
        st = {k: float(prior_state.get(k, 0.0)) for k in KEYS}
    if prior_rows or prior_state:
        st = carry(st, delta)
    for r in rows:
        st = step(st, r, lam)
    return st


# ---- rates -----------------------------------------------------------------
def share_prior(table, pos, price):
    """Prior share of his side's xG per 90 -- capped, so a fitted line cannot run away."""
    a, b = table[pos]
    return min(math.exp(a + b * price), 0.6)


def rates(st, pos, price, p=None):
    kg, ka, ksv, kdc, ky = (_p(p, n) for n in ("K_G", "K_A", "K_SV", "K_DC", "K_Y"))
    return {
        "sg": (st["X"] + kg * share_prior(_p(p, "SHARE_G"), pos, price)) / (st["E"] + kg),
        "sa": (st["A"] + ka * share_prior(_p(p, "SHARE_A"), pos, price)) / (st["E"] + ka),
        "sv": (st["SV"] + ksv * _p(p, "SAVE_RATE")) / (st["O"] + ksv),
        "dc": (st["D60"] + kdc * _p(p, "DC_RATE")[pos]) / (st["N60"] + kdc),
        "yc": (st["YC"] + ky * _p(p, "YC90")[pos]) / (st["M90"] + ky),
        "rc": (st["RC"] + ky * _p(p, "RC90")[pos]) / (st["M90"] + ky),
        "misc": (st["MISC"] + ky * _p(p, "MISC90")[pos]) / (st["M90"] + ky),
    }


# ---- Poisson helpers -------------------------------------------------------
def _floor_div_mean(lam, k):
    """E[floor(X / k)] for X ~ Poisson(lam)."""
    if lam <= 0:
        return 0.0
    # E[floor(X/k)] = sum_{j>=1} P(X >= j k)
    total, pmf, cdf, x = 0.0, math.exp(-lam), 0.0, 0
    cdf = pmf
    j = 1
    limit = int(lam + 12 * math.sqrt(lam) + 20)
    while x < limit:
        x += 1
        pmf *= lam / x
        if x % k == 0:
            total += 1.0 - cdf         # P(X >= x) with x = j k
            j += 1
        cdf += pmf
    return total


# ---- one fixture -----------------------------------------------------------
def expected(r, mins, gf, ga, pos, defcon=True, p=None):
    """
    r:      rates() for the player
    mins:   minutes.py forecast for this fixture (start, p60, app, xmin),
            availability already applied
    gf, ga: his side's expected goals for and against in this fixture
    defcon: whether the season scores defensive contributions
    Returns the components and their total, in FPL points.
    """
    f = mins["xmin"] / 90.0
    p60, app = mins["p60"], mins["app"]
    eg = r["sg"] * gf * f
    ea = r["sa"] * gf * f * _p(p, "ASSIST_CAL")
    pcs = math.exp(-ga)
    out = {
        "app": app + p60,
        "goals": GOAL[pos] * eg,
        "assists": 3.0 * ea,
        "cs": CLEAN[pos] * p60 * pcs,
        "gc": -p60 * _floor_div_mean(ga, 2) if pos in ("GK", "DEF") else 0.0,
        "saves": p60 * _floor_div_mean(r["sv"] * ga, 3) if pos == "GK" else 0.0,
        "defcon": 2.0 * p60 * r["dc"] if (defcon and DEFCON[pos]) else 0.0,
        "cards": -(r["yc"] + 3.0 * r["rc"]) * f,
        "misc": r["misc"] * f,
    }
    b = _p(p, "BONUS")[pos]
    # A straight-line fit can dip below nothing for a player with little
    # chance of a return; bonus cannot, so the expectation is floored at 0.
    out["bonus"] = max(0.0, b[0] * p60 + b[1] * eg + b[2] * ea + b[3] * p60 * pcs
                       + b[4] * (out["saves"]) + b[5] * (p60 * r["dc"] if defcon else 0.0))
    out["total"] = sum(out.values())
    return out


# ---- the side's expected goals: model.js, in Python ------------------------
# The same numbers the ticker shows. model.test.mjs freezes model.js's output
# in model.fixture.json; points_test.py checks this port against that file.
def strengths(teams, matches_played, kA, kD, kp=4):
    b = {}
    n = matches_played
    for tid, r in teams.items():
        wa = n / (n + (kp if r.get("promoted") else kA))
        wd = n / (n + (kp if r.get("promoted") else kD))
        b[tid] = (wa * r["a26"] + (1 - wa) * r["pa"], wd * r["d26"] + (1 - wd) * r["pd"])
    la = sum(v[0] for v in b.values()) / len(b)
    ld = sum(v[1] for v in b.values()) / len(b)
    return {"ATK": {t: v[0] / la for t, v in b.items()},
            "DEF": {t: v[1] / ld for t, v in b.items()},
            "base": (la + ld) / 2}


def side_goals(S, team, opp, home, gw, home_mult, pen, market=None):
    """
    (goals for, goals against) for `team` v `opp`: rawVal(..., "proj") for
    both sides of model.js. The bookmakers' number wins where there is one,
    exactly as on the ticker. market: {gw: {team id: expected goals}}.
    """
    m = home_mult if home else 1 / home_mult
    gf = S["base"] * S["ATK"][team] * S["DEF"][opp] * m * pen
    ga = S["base"] * S["ATK"][opp] * S["DEF"][team] / m * pen
    g = (market or {}).get(gw) or {}
    if g.get(team) is not None:
        gf = g[team]
    if g.get(opp) is not None:
        ga = g[opp]
    return gf, ga


# ---- reading FPL's live replies --------------------------------------------
def _stat(e, key):
    try:
        return float((e.get("stats") or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def live_stat_rows(lives, finished):
    """
    {(player id, fixture id): row} for every finished fixture a player
    appeared in, from event/{gw}/live/. FPL gives expected goals, saves,
    DefCon and cards per gameweek, not per fixture: in a double gameweek they
    are split between the two games by minutes played. Rows are the shape
    step() wants, less tf / ta, which need the side totals (side_xg_live()).
    """
    out = {}
    for gw in sorted(lives):
        for e in lives[gw].get("elements", []):
            fx = []
            for x in e.get("explain", []):
                if x["fixture"] not in finished:
                    continue
                mins = next((s["value"] for s in x["stats"] if s["identifier"] == "minutes"), 0)
                fx.append((x["fixture"], mins))
            tot = sum(m for _, m in fx)
            if tot <= 0:
                continue
            for f, m in fx:
                if m <= 0:
                    continue
                w = m / tot
                out[(e["id"], f)] = dict(
                    m=m, xg=_stat(e, "expected_goals") * w, xa=_stat(e, "expected_assists") * w,
                    sv=_stat(e, "saves") * w, dc=_stat(e, "defensive_contribution") * w,
                    yc=_stat(e, "yellow_cards") * w, rc=_stat(e, "red_cards") * w,
                    misc=(5 * _stat(e, "penalties_saved") - 2 * _stat(e, "penalties_missed")
                          - 2 * _stat(e, "own_goals")) * w,
                    key=finished[f])
    return out


def row_sides(stat_rows, sides, team_of):
    """
    {(player, fixture): the team he played for}. Usually his current team.
    A player who has since changed clubs has rows in fixtures his new side
    was not in; his old club is the one side common to all of those (two
    or more are needed to be sure). A game between his old and new clubs
    goes to whichever he was at on the day: the old one until his first game
    for the new one. None where it cannot be told.
    """
    by_p = collections.defaultdict(list)
    for (pid, f), r in stat_rows.items():
        by_p[pid].append((r["key"], f))
    out = {}
    for pid, fx in by_p.items():
        t = team_of.get(pid)
        away = [f for _, f in fx if t not in sides.get(f, ())]
        old = None
        if len(away) >= 2:
            common = set.intersection(*(set(sides.get(f, ())) for f in away))
            if len(common) == 1:
                old = common.pop()
        first_new = min((k for k, f in fx if t in sides.get(f, ()) and old not in sides.get(f, ())),
                        default=None)
        for k, f in fx:
            sd = sides.get(f, ())
            if t in sd and old in sd:            # his old club against his new one
                out[(pid, f)] = t if (first_new is not None and k >= first_new) else old
            elif t in sd:
                out[(pid, f)] = t
            else:
                out[(pid, f)] = old if old in sd else None
    return out


def side_xg_live(stat_rows, sides, team_of, placed=None):
    """{(fixture, team): xG} summed from the players, each on the side he played for."""
    placed = placed if placed is not None else row_sides(stat_rows, sides, team_of)
    tot = {}
    for (pid, f), r in stat_rows.items():
        t = placed.get((pid, f))
        if t is not None:
            tot[(f, t)] = tot.get((f, t), 0.0) + r["xg"]
    return tot
