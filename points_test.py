"""
Tests for points.py. No network, nothing to install:

    python points_test.py

The accuracy of the model is the backtest's job (claude/backtest-points.md).
These check that the Python copy of the fixture model gives exactly the
numbers model.js gives, that the scoring arithmetic is right, and that FPL's
replies are read correctly.
"""
import json, math, pathlib, random, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import points as PT

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL  {name}  {detail}")


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


# ---- the fixture model: identical to model.js ------------------------------
# model.fixture.json holds model.js's own output, frozen. Every value checked
# here is one the ticker actually shows.
F = json.loads((HERE / "model.fixture.json").read_text())
IN, EX = F["input"], F["expected"]
teams = {k: dict(v, **({"promoted": 1} if v.get("promoted") else {})) for k, v in IN["teams"].items()}
ids = sorted(teams, key=int)

for e in EX["strengths"]:
    kA, kD, kp = e["k"]
    S = PT.strengths(teams, IN["matchesPlayed"], kA, kD, kp)
    ok = close(S["base"], e["base"], 5e-7) and all(
        close(S["ATK"][i], e["ATK"][i], 5e-7) and close(S["DEF"][i], e["DEF"][i], 5e-7) for i in ids)
    check(f"strengths match model.js at k = {kA}/{kD}/{kp}", ok)

S = PT.strengths(teams, IN["matchesPlayed"], IN["fit"]["kAtk"], IN["fit"]["kDef"], 4)
fx = {i: [] for i in ids}
for gw, h, a, hd, ad in IN["fixtures"]:
    fx[str(h)].append((gw, str(a), True)); fx[str(a)].append((gw, str(h), False))
for i in ids:
    fx[i].sort(key=lambda x: x[0])
for key, want in EX["values"].items():
    mode, home = key.split("@")
    if mode != "proj":
        continue
    k = worst = 0
    for i in ids:
        for gw, opp, is_home in fx[i]:
            gf, ga = PT.side_goals(S, i, opp, is_home, gw, float(home), IN["fit"]["pen"])
            worst = max(worst, abs(gf - want["atk"][k]), abs(math.exp(-ga) * 100 - want["def"][k]))
            k += 1
    check(f"every fixture's xG and clean sheet match model.js ({key}, {k} fixtures)",
          worst <= 5e-7 and k == len(want["atk"]) and k > 300, f"worst {worst}")

market = {int(g): v for g, v in EX["market"].items()}
worst = 0
for c in EX["marketCases"]:
    gf, ga = PT.side_goals(S, str(c["team"]), str(c["opp"]), bool(c["home"]), c["gw"],
                           IN["fit"]["home"], IN["fit"]["pen"], market)
    v = gf if c["side"] == "atk" else math.exp(-ga) * 100
    worst = max(worst, abs(v - c["v"]))
    gf0, ga0 = PT.side_goals(S, str(c["team"]), str(c["opp"]), bool(c["home"]), c["gw"],
                             IN["fit"]["home"], IN["fit"]["pen"], None)
    worst = max(worst, abs((gf0 if c["side"] == "atk" else math.exp(-ga0) * 100) - c["unpriced"]))
check(f"the bookmakers' number wins where there is one, as on the ticker ({len(EX['marketCases'])} cases)",
      worst <= 5e-7, worst)

# ---- arithmetic --------------------------------------------------------------
rng = random.Random(1)
for lam, k in ((0.3, 2), (1.4, 2), (2.9, 2), (0.8, 3), (4.0, 3), (7.5, 3)):
    # exact by summing the Poisson distribution directly
    pmf, exact = math.exp(-lam), 0.0
    for x in range(0, 80):
        if x:
            pmf *= lam / x
        exact += (x // k) * pmf
    check(f"E[floor(X/{k})], X ~ Poisson({lam})", close(PT._floor_div_mean(lam, k), exact, 1e-9),
          (PT._floor_div_mean(lam, k), exact))
check("E[floor(X/2)] of nothing is nothing", PT._floor_div_mean(0.0, 2) == 0.0)

st = PT.record([dict(m=90, xg=0.5, xa=0.2, tf=1.5, ta=1.0, sv=0, dc=11, yc=1, rc=0, misc=0, pos="DEF"),
                dict(m=0, xg=0, xa=0, tf=1, ta=1, sv=0, dc=0, yc=0, rc=0, misc=0, pos="DEF"),
                dict(m=45, xg=0.1, xa=0.0, tf=1.2, ta=0.8, sv=0, dc=3, yc=0, rc=0, misc=0, pos="DEF")], lam=0.5)
check("record: a game he did not play is skipped", close(st["M90"], 0.5 * 1 + 0.5))
check("record: xG decayed", close(st["X"], 0.5 * 0.5 + 0.1))
check("record: exposure is his side's xG times his share of the 90", close(st["E"], 0.5 * 1.5 + 1.2 * 0.5))
check("record: DefCon counts only 60-minute games", close(st["N60"], 0.5) and close(st["D60"], 0.5))
carried = PT.record([], prior_state={"X": 2.0, "E": 4.0}, delta=0.25)
check("record: last season's stored state is discounted", close(carried["X"], 0.5) and close(carried["E"], 1.0))

r = PT.rates(PT.empty(), "MID", 8.0)
check("rates: no record -> the price prior", close(r["sg"], PT.share_prior(PT.SHARE_G, "MID", 8.0)))
check("rates: a dearer midfielder is expected to take more of the shots",
      PT.rates(PT.empty(), "MID", 10.0)["sg"] > PT.rates(PT.empty(), "MID", 5.0)["sg"])
check("share prior is capped", PT.share_prior({"FWD": (0.0, 1.0)}, "FWD", 50.0) == 0.6)

mins = {"start": 0.9, "p60": 0.85, "app": 0.95, "xmin": 80.0}
none = {"start": 0.0, "p60": 0.0, "app": 0.0, "xmin": 0.0}
for pos in PT.POSITIONS:
    rr = PT.rates(PT.empty(), pos, 6.0)
    e = PT.expected(rr, mins, 1.6, 1.1, pos)
    check(f"{pos}: components add up to the total", close(sum(v for k, v in e.items() if k != "total"), e["total"]))
    check(f"{pos}: a player who will not play scores nothing",
          all(abs(v) < 1e-12 for v in PT.expected(rr, none, 1.6, 1.1, pos).values()))
    better = PT.expected(rr, mins, 2.2, 0.6, pos)["total"]
    check(f"{pos}: an easier fixture is worth more", better > e["total"], (better, e["total"]))
    check(f"{pos}: appearance points are P(appears) + P(60+)", close(e["app"], 0.95 + 0.85))
gk = PT.expected(PT.rates(PT.empty(), "GK", 5.0), mins, 1.4, 1.2, "GK")
check("GK: clean sheet is P(60+) x exp(-goals against) x 4", close(gk["cs"], 4 * 0.85 * math.exp(-1.2)))
check("GK: saves and goals conceded counted", gk["saves"] > 0 and gk["gc"] < 0)
fwd = PT.expected(PT.rates(PT.empty(), "FWD", 7.0), mins, 1.4, 1.2, "FWD")
check("FWD: no clean sheet, no goals conceded, no saves", fwd["cs"] == 0 and fwd["gc"] == 0 and fwd["saves"] == 0)
check("no DefCon in a season that does not score it",
      PT.expected(PT.rates(PT.empty(), "DEF", 5.0), mins, 1.4, 1.2, "DEF", defcon=False)["defcon"] == 0)

# ---- reading FPL's replies -------------------------------------------------
def el(pid, stats, *fx):
    return {"id": pid, "stats": stats,
            "explain": [{"fixture": f, "stats": [{"identifier": "minutes", "value": m}]} for f, m in fx]}


lives = {1: {"elements": [
    el(1, {"expected_goals": "0.60", "expected_assists": "0.30", "saves": 0, "defensive_contribution": 12,
           "yellow_cards": 1}, (10, 90)),
    el(2, {"expected_goals": "0.30"}, (10, 30)),
    el(3, {}, (10, 0)),
]}, 2: {"elements": [
    el(1, {"expected_goals": "0.90", "defensive_contribution": 9}, (20, 60), (21, 30)),   # a double
    el(2, {"expected_goals": "0.20"}, (20, 90), (22, 45)),                                # 22 unfinished
]}}
finished = {10: (1, "a"), 20: (2, "b"), 21: (2, "c")}
rows = PT.live_stat_rows(lives, finished)
check("live: one row per appearance, none for a 0-minute entry", set(rows) == {(1, 10), (2, 10), (1, 20), (1, 21), (2, 20)}, set(rows))
check("live: strings read as numbers", close(rows[(1, 10)]["xg"], 0.6) and close(rows[(1, 10)]["xa"], 0.3))
check("live: a double splits the gameweek's xG by minutes",
      close(rows[(1, 20)]["xg"], 0.6) and close(rows[(1, 21)]["xg"], 0.3))
check("live: an unfinished fixture is ignored, and does not dilute the split",
      close(rows[(2, 20)]["xg"], 0.2 * 90 / 90))
sxg = PT.side_xg_live(rows, {10: (1, 2), 20: (1, 2), 21: (2, 1)}, {1: 1, 2: 2})
check("side totals by current team", close(sxg[(10, 1)], 0.6) and close(sxg[(10, 2)], 0.3), sxg)


# ---- each component, by hand -------------------------------------------------
full = {"start": 1.0, "p60": 1.0, "app": 1.0, "xmin": 90.0}
R0 = {"sg": 0.2, "sa": 0.1, "sv": 2.0, "dc": 0.3, "yc": 0.1, "rc": 0.02, "misc": 0.05}
check("FPL's goal values: 10 / 6 / 5 / 4", PT.GOAL == {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4})
check("FPL's clean sheet values: 4 / 4 / 1 / 0", PT.CLEAN == {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0})
check("DefCon thresholds: 10 for defenders, 12 for the rest", PT.DEFCON == {"GK": None, "DEF": 10, "MID": 12, "FWD": 12})
gf, ga = 1.5, 1.2
for pos in PT.POSITIONS:
    e = PT.expected(R0, full, gf, ga, pos)
    check(f"{pos}: goals = value x share x side's xG", close(e["goals"], PT.GOAL[pos] * 0.2 * gf))
    check(f"{pos}: assists = 3 x share x side's xG x calibration", close(e["assists"], 3 * 0.1 * gf * PT.ASSIST_CAL))
    check(f"{pos}: cards = -1 a yellow, -3 a red", close(e["cards"], -(0.1 + 3 * 0.02)))
    check(f"{pos}: penalties and own goals", close(e["misc"], 0.05))
    check(f"{pos}: DefCon is 2 points a hit, none for keepers",
          close(e["defcon"], 0.0 if pos == "GK" else 2 * 0.3))
    check(f"{pos}: goals conceded only for keepers and defenders",
          close(e["gc"], -PT._floor_div_mean(ga, 2) if pos in ("GK", "DEF") else 0.0))
    b = PT.BONUS[pos]
    want = max(0.0, b[0] + b[1] * 0.2 * gf + b[2] * 0.1 * gf * PT.ASSIST_CAL + b[3] * math.exp(-ga)
               + b[4] * e["saves"] + b[5] * (0.3 if PT.DEFCON[pos] else 0.0))
    check(f"{pos}: bonus from the fitted table, term by term", close(e["bonus"], want), (e["bonus"], want))
check("GK: 1 point per 3 saves", close(PT.expected(R0, full, gf, ga, "GK")["saves"], PT._floor_div_mean(2.0 * ga, 3)))
check("bonus is never negative",
      PT.expected(dict(R0, sg=0, sa=0, sv=0), full, 0.1, 4.0, "GK")["bonus"] == 0.0
      and PT.BONUS["GK"][0] + PT.BONUS["GK"][3] * math.exp(-4.0) < 0)
check("a quarter of the game is a quarter of the goals",
      close(PT.expected(R0, dict(full, xmin=22.5), gf, ga, "MID")["goals"], PT.GOAL["MID"] * 0.2 * gf / 4))
e0 = PT.rates(PT.empty(), "DEF", 5.0)
check("rates: yellow and red cards from their own tables",
      close(e0["yc"], PT.YC90["DEF"]) and close(e0["rc"], PT.RC90["DEF"]) and PT.YC90["DEF"] != PT.RC90["DEF"])
check("rates: assists from the assist table", close(e0["sa"], PT.share_prior(PT.SHARE_A, "DEF", 5.0)))
check("rates: a record of his own moves him off the prior",
      PT.rates(PT.record([], prior_state={"X": 6.0, "E": 10.0}, delta=1.0), "DEF", 5.0)["sg"] > 2 * e0["sg"])

# ---- players who changed clubs --------------------------------------------------
# 7 moved from club 3 to club 1. Fixtures: 30 = 3 v 4, 31 = 5 v 3, 32 = 3 v 1 (before
# his move), 33 = 1 v 6 (after), 34 = 2 v 1 (after).
sd = {30: (3, 4), 31: (5, 3), 32: (3, 1), 33: (1, 6), 34: (2, 1), 40: (8, 9)}
rw = lambda k, xg=0.4: dict(m=90, xg=xg, xa=0, sv=0, dc=0, yc=0, rc=0, misc=0, key=k)
rows_m = {(7, 30): rw((1, "a")), (7, 31): rw((2, "a")), (7, 32): rw((3, "a")), (7, 33): rw((4, "a")),
          (7, 34): rw((5, "a")), (8, 32): rw((3, "a"), 0.3), (9, 40): rw((1, "a"))}
pl_ = PT.row_sides(rows_m, sd, {7: 1, 8: 1, 9: 1})
check("movers: games for his old club go to his old club",
      pl_[(7, 30)] == 3 and pl_[(7, 31)] == 3, pl_)
check("movers: old club v new club, before the move, is the old club", pl_[(7, 32)] == 3, pl_)
check("movers: games for his new club stay with it", pl_[(7, 33)] == 1 and pl_[(7, 34)] == 1)
check("movers: a player who did not move is where he is", pl_[(8, 32)] == 1)
check("movers: one stray game cannot be placed", pl_[(9, 40)] is None)
sx = PT.side_xg_live(rows_m, sd, {7: 1, 8: 1, 9: 1})
check("movers: his old side keeps his xG", close(sx[(32, 3)], 0.4) and close(sx[(32, 1)], 0.3), sx)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
