"""
Tests for minutes.py and update.build_minutes. No network, nothing to install:

    python minutes_test.py

The accuracy of the model is not tested here -- that is the backtest's job
(claude/backtest-minutes.md). These check the arithmetic, the rules, the
reading of FPL's replies, and that nothing moves by accident.
"""
import collections, datetime, math, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import minutes as MN

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


def consistent(o, tag):
    check(f"{tag}: p60 <= start <= app", o["p60"] <= o["start"] + 1e-12 and o["start"] <= o["app"] + 1e-12, o)
    check(f"{tag}: probabilities in 0-1", all(0 <= o[k] <= 1 + 1e-12 for k in ("start", "p60", "app")), o)
    if "xs" in o:
        check(f"{tag}: xmin = xs + xb", close(o["xmin"], o["xs"] + o["xb"], 1e-9), o)
    check(f"{tag}: 0 <= xmin <= 90", 0 <= o["xmin"] <= 90, o)


# ---- memory arithmetic -----------------------------------------------------
st = MN.memory([(1, 90), (0, 0), (0, 25)], 0.5)
# oldest first: weights 0.25, 0.5, 1
check("memory: n", close(st["n"], 1.75))
check("memory: starts", close(st["s"], 0.25))
check("memory: 60+ starts", close(st["s60"], 0.25))
check("memory: sub appearances", close(st["b"], 1.0))
check("memory: sub minutes", close(st["mb"], 25.0))
check("memory: minutes when starting", close(st["ms"], 22.5))

carried = MN.memory([(1, 90)], 0.5, prior_rows=[(1, 90), (1, 90)], delta=0.25)
# last season: 0.5 + 1 = 1.5, discounted to 0.375; then one fixture: 0.375 * 0.5 + 1
check("memory: last season carried at delta", close(carried["n"], 0.375 * 0.5 + 1))
check("memory: no prior rows means no carry", MN.memory([], 0.5) == MN.empty())

# ---- rates and outcomes ----------------------------------------------------
ps, q, r, ms, mb = MN.rates(MN.empty(), "MID", 5.0)
check("rates: nobody seen play -> start prior", close(ps, MN.start_prior("MID", 5.0)))
check("rates: nobody seen play -> 60+ prior", close(q, MN.Q_PRIOR["MID"]))
check("rates: nobody seen play -> bench prior", close(r, MN.R_PRIOR["MID"]))
check("start prior rises with price", MN.start_prior("DEF", 6.0) > MN.start_prior("DEF", 4.0))

o = MN.outcome(0.8, 0.9, 0.3, 85, 20)
consistent(o, "outcome")
check("outcome: app", close(o["app"], 0.8 + 0.2 * 0.3))
check("outcome: xmin", close(o["xmin"], 0.8 * 85 + 0.2 * 0.3 * 20))

# ---- rescale / calibrate ---------------------------------------------------
for new in (0.0, 0.3, 0.8, 0.999):
    consistent(MN.rescale(o, new), f"rescale to {new}")
check("rescale: start set", close(MN.rescale(o, 0.5)["start"], 0.5))
check("rescale: bench rate kept", close((MN.rescale(o, 0.5)["app"] - 0.5) / 0.5, 0.3))
ident = {"CAL": [(0.0, 1.0)] * 8}
check("calibrate: identity leaves it alone",
      all(close(MN.calibrate(o, 3, ident)[k], o[k]) for k in o))
lo = MN.calibrate(MN.outcome(0.3, .9, .3, 85, 20), 4)["start"]
hi = MN.calibrate(MN.outcome(0.7, .9, .3, 85, 20), 4)["start"]
check("calibrate: keeps the order", lo < hi)
check("calibrate: further out is less sure of a regular",
      MN.calibrate(MN.outcome(0.95, .9, .3, 85, 20), 8)["start"] <
      MN.calibrate(MN.outcome(0.95, .9, .3, 85, 20), 1)["start"])
check("W: one weight per horizon, falling", all(a >= b for a, b in zip(MN.W, MN.W[1:])))
check("W: beyond the list uses the last", MN.weight(30) == MN.W[-1])
check("CAL: one per weight", len(MN.CAL) == len(MN.W))

# ---- forecast --------------------------------------------------------------
regular = MN.fold([(1, 90)] * 5, [(1, 90)] * 30)
bench = MN.fold([(0, 0)] * 5, [(0, 0)] * 30)
f1 = MN.forecast(regular, "DEF", 5.0, 1)
consistent(f1, "forecast regular")
check("forecast: regular likely starts", f1["start"] > 0.85, f1)
check("forecast: unused sub unlikely to start", MN.forecast(bench, "DEF", 5.0, 1)["start"] < 0.1)
check("forecast: availability 0 zeroes everything",
      all(v == 0 for v in MN.forecast(regular, "DEF", 5.0, 1, avail=0.0).values()))
half = MN.forecast(regular, "DEF", 5.0, 1, avail=0.5)
check("forecast: availability scales everything",
      all(close(half[k], f1[k] * 0.5) for k in f1))
dropped = MN.fold([(1, 90)] * 8 + [(0, 0)] * 2)
check("forecast: dropped for two games -> short memory bites next week",
      MN.forecast(dropped, "MID", 6.0, 1)["start"] < MN.forecast(dropped, "MID", 6.0, 8)["start"])

# Pinned values. These change only if the constants or the maths do -- which
# must be deliberate, with a rerun of the backtest. Regenerate by printing
# them and say so in the commit.
PIN = [(MN.fold([(1, 90), (1, 78), (0, 12), (1, 90)], [(1, 90)] * 20 + [(0, 0)] * 5), "MID", 6.5, 1,
        {"start": 0.696695, "p60": 0.681965, "app": 0.862606, "xmin": 62.529783}),
       (MN.fold([(0, 0), (0, 0), (0, 20)], []), "FWD", 5.5, 4,
        {"start": 0.046287, "p60": 0.042339, "app": 0.438858, "xmin": 11.157297})]
for i, (mem, pos, price, h, want) in enumerate(PIN):
    got = MN.forecast(mem, pos, price, h)
    for k, v in want.items():
        check(f"pinned value {i} {k}", close(got[k], v, 1e-6), f"{got[k]:.6f} != {v}")

# ---- line-up fill ----------------------------------------------------------
outs = [MN.outcome(p, 0.9, 0.2, 80, 18) for p in (0.95, 0.9, 0.85, 0.3, 0.1, 0.0)]
for tgt in (2.0, 3.0, 3.5):
    filled = MN.fill_lineup(outs, tgt)
    check(f"fill: adds up to {tgt}", close(sum(o["start"] for o in filled), tgt, 1e-6),
          sum(o["start"] for o in filled))
    check(f"fill {tgt}: ruled-out player stays at 0", filled[-1]["start"] == 0)
    check(f"fill {tgt}: order kept", all(a["start"] >= b["start"] for a, b in zip(filled, filled[1:])))
    for j, o2 in enumerate(filled):
        consistent(o2, f"fill {tgt} player {j}")
up = MN.fill_lineup(outs, 3.5)
check("fill: a 30% player moves more than a 95% one",
      up[3]["start"] - outs[3]["start"] > up[0]["start"] - outs[0]["start"])
short = MN.fill_lineup(outs, 5.0)
check("fill: a side with exactly enough players starts them all, short of certainty",
      4.8 < sum(o["start"] for o in short) < 5.0 and max(o["start"] for o in short) < 1.0,
      [round(o["start"], 3) for o in short])
nobody = MN.fill_lineup([MN.outcome(0.001, .9, .1, 80, 18)], 1.0)[0]["start"]
check("fill: a player with no record at all is not pushed to certainty", nobody < 0.5, nobody)
check("fill: nobody available -> unchanged",
      MN.fill_lineup([MN.outcome(0, .9, .2, 80, 18)], 1.0)[0]["start"] == 0)
# A doubtful player (25%) who is his group's only option must stay at or
# under 25% -- the review found the fill lifting him to 100%.
doubt = {k: v * 0.25 for k, v in MN.outcome(0.9, 0.9, 0.2, 85, 20).items()}
lone = MN.fill_lineup([doubt], 1.0, [0.25])[0]
check("fill: never above a player's availability", lone["start"] <= 0.25 + 1e-12, lone)
consistent(lone, "fill doubtful")
pair = MN.fill_lineup([doubt, MN.outcome(0.05, .99, .01, 90, 30)], 1.0, [0.25, 1.0])
check("fill: the fit backup keeper takes up the doubtful keeper's slack",
      pair[1]["start"] > 0.5 and pair[0]["start"] <= 0.25 + 1e-12, pair)
# The shift is one common step on each player's log-odds of starting GIVEN he
# is available. Solve it independently here and compare.
fitm = MN.outcome(0.5, 0.9, 0.2, 80, 18)
got = MN.fill_lineup([doubt, fitm], 0.9, [0.25, 1.0])
lg = lambda x: math.log(x / (1 - x)); sg = lambda z: 1 / (1 + math.exp(-z))
lo_, hi_ = -10.0, 10.0
for _ in range(100):
    d = (lo_ + hi_) / 2
    if 0.25 * sg(lg(0.9) + d) + sg(lg(0.5) + d) < 0.9: lo_ = d
    else: hi_ = d
check("fill: one common log-odds step, on the given-available scale",
      close(got[0]["start"], 0.25 * sg(lg(0.9) + d), 1e-6) and close(got[1]["start"], sg(lg(0.5) + d), 1e-6),
      (got[0]["start"], got[1]["start"], 0.25 * sg(lg(0.9) + d), sg(lg(0.5) + d)))
check("groups: keeper apart, outfield pooled",
      MN.group("GK") == "GK" and {MN.group(p) for p in ("DEF", "MID", "FWD")} == {"OUT"})
check("line-up: one keeper and ten outfield", MN.LINEUP == {"GK": 1.0, "OUT": 10.0})

# ---- FPL's news --------------------------------------------------------------
today = datetime.date(2026, 9, 22)
check("date: expected back", MN.return_date("Knee injury - Expected back 10 Oct", today) == datetime.date(2026, 10, 10))
check("date: suspended until", MN.return_date("Suspended until 19 Oct", today) == datetime.date(2026, 10, 19))
check("date: single-digit day", MN.return_date("Foot injury - Expected back 7 Nov", today) == datetime.date(2026, 11, 7))
check("date: January means next year",
      MN.return_date("Expected back 7 Jan", datetime.date(2026, 12, 20)) == datetime.date(2027, 1, 7))
check("date: unknown -> None", MN.return_date("Back injury - Unknown return date", today) is None)
check("date: nonsense date -> None", MN.return_date("Expected back 31 Feb", today) is None)
d6, d8 = datetime.date(2026, 10, 10), datetime.date(2026, 10, 24)
A = MN.availability
check("avail: fit", A("a", None, "", d6, today, True) == 1.0)
check("avail: 75% next round", A("d", 75, "Muscular injury - 75% chance of playing", d6, today, True) == 0.75)
check("avail: 75% later -> fit", A("d", 75, "Muscular injury - 75% chance of playing", d8, today, False) == 1.0)
check("avail: left the club -> out for good", A("u", 0, "Has joined Al Hilal permanently", d8, today, False) == 0.0)
check("avail: injured, no date -> out", A("i", 0, "Unknown return date", d8, today, False) == 0.0)
check("avail: next round follows FPL's percentage even with a date",
      A("i", 0, "Expected back 10 Oct", d6, today, True) == 0.0)
check("avail: after next round, back on the day of the game -> available",
      A("i", 0, "Expected back 10 Oct", d6, today, False) == 1.0)
check("avail: before the return date -> out", A("i", 0, "Expected back 7 Nov", d8, today, False) == 0.0)
check("avail: suspension served -> available", A("s", 0, "Suspended until 19 Oct", d8, today, False) == 1.0)
check("date: a date a few weeks old stays in the past",
      MN.return_date("Expected back 20 Jul", today) == datetime.date(2026, 7, 20))
check("date: late December read in January is the one just gone",
      MN.return_date("Expected back 28 Dec", datetime.date(2027, 1, 5)) == datetime.date(2026, 12, 28))
check("date: a long-term injury is next spring, not last",
      MN.return_date("Expected back 20 Apr", today) == datetime.date(2027, 4, 20))
check("avail: long-term injury -> out for months",
      A("i", 0, "Expected back 20 Apr", d8, today, False) == 0.0)
check("avail: still injured past a stale date -> out until FPL updates",
      A("i", 0, "Expected back 20 Jul", d8, today, False) == 0.0)
check("avail: doubtful past a stale date -> fit later on",
      A("d", 75, "Expected back 20 Jul", d8, today, False) == 1.0)
check("avail: a leftover date on a fit player is ignored",
      A("a", None, "Expected back 20 Apr", d8, today, False) == 1.0)

# ---- reading FPL's replies ---------------------------------------------------
def el(pid, starts, *fx):
    return {"id": pid, "stats": {"starts": starts},
            "explain": [{"fixture": f, "stats": [{"identifier": "minutes", "value": m}]} for f, m in fx]}


lives = {1: {"elements": [el(1, 1, (10, 90)), el(2, 0, (10, 0)), el(3, 0, (10, 30))]},
         2: {"elements": [el(1, 1, (20, 70), (21, 20)), el(2, 2, (20, 90), (21, 88)),
                          el(3, 0, (22, 45))]}}
finished = {10: (1, "2026-08-20T19:00:00Z"), 20: (2, "2026-08-27T15:00:00Z"),
            21: (2, "2026-08-30T15:00:00Z")}                            # 22 not finished
fr = MN.live_fixture_rows(lives, finished)
rows = MN.live_rows(fr)
check("live: one row per team fixture, 0 minutes included", rows[2][0] == (0, 0))
check("live: unfinished fixture left out", len(rows[3]) == 1, rows[3])
check("live: double gameweek, start credited to the longer game", rows[1] == [(1, 90), (1, 70), (0, 20)], rows[1])
check("live: double gameweek, two starts", rows[2][1:] == [(1, 90), (1, 88)], rows[2])
check("live: sub appearance is not a start", rows[3] == [(0, 30)])
check("prior decoding", MN.decode_prior([190, 12, 100, 0]) == [(1, 90), (0, 12), (1, 0), (0, 0)])

# ---- update.build_minutes, end to end on a made-up round -------------------
try:
    import update
except Exception as e:                       # pragma: no cover
    update = None
    check("update.py imports without the network", False, repr(e))

if update:
    update.log = lambda *a, **k: None                # keep the test output clean
    # 22 per side. Index within a side:
    #   0 GK1*  1 GK2   2-5 DEF*  6-8 DEF   9-12 MID*  13-16 MID   17-18 FWD*  19-21 FWD
    # (* = starts). Player id = 22 * (side - 1) + index + 1.
    POS = [1, 1] + [2] * 7 + [3] * 8 + [4] * 5
    STARTERS = {0, 2, 3, 4, 5, 9, 10, 11, 12, 17, 18}
    pid = lambda side, idx: 22 * (side - 1) + idx + 1

    def squad():
        return [{"id": pid(t, i), "code": 9000 + pid(t, i), "element_type": et, "now_cost": 55,
                 "team": t, "status": "a", "chance_of_playing_next_round": None, "news": "",
                 "_starter": i in STARTERS}
                for t in (1, 2) for i, et in enumerate(POS)]

    def fx(fid, gw, h, a, day, done):
        return {"id": fid, "event": gw, "team_h": h, "team_a": a,
                "kickoff_time": f"2026-{day}T15:00:00Z", "finished": done}

    elements = squad()
    by_id = {e["id"]: e for e in elements}
    INJ, BENCH_DEF_1, BENCH_DEF_2 = pid(1, 2), pid(1, 6), pid(2, 6)
    by_id[INJ].update(status="i", chance_of_playing_next_round=0, news="Knee injury - Unknown return date")
    KEEPER, COVER = pid(2, 0), pid(2, 1)
    by_id[KEEPER].update(status="d", chance_of_playing_next_round=25, news="Knock - 25% chance of playing")
    by_id[COVER].update(status="u", chance_of_playing_next_round=0, news="Has joined Rangers on loan")
    live_el = [el(e["id"], int(e["_starter"]), (1, 90 if e["_starter"] else 0)) for e in elements]
    boot = {"elements": elements}
    # GW1 played; GW2 a double for both sides; GW3 blank; GW4 one game
    raw_fx = [fx(1, 1, 1, 2, "08-20", True), fx(2, 2, 2, 1, "08-27", False),
              fx(3, 2, 1, 2, "08-30", False), fx(4, 4, 2, 1, "09-13", False)]
    calls = []

    def fetch(url):
        calls.append(url)
        return {"elements": live_el}

    mins, summ = update.build_minutes(boot, raw_fx, 2, 2026, today=datetime.date(2026, 8, 22), fetch=fetch)
    S1 = pid(1, 3)                                     # a fit starting defender
    check("build: fetches only played gameweeks", calls == [f"{update.FPL}/event/1/live/"], calls)
    check("build: every player gets a record", len(mins) == 44)
    check("build: gameweek list starts at the next one", summ["gws"] == list(range(2, 2 + update.MINUTES_HORIZON)), summ)
    check("build: a double gameweek does not trip the checks, and counts both games",
          mins[S1]["xm"][0] > 1.6 * mins[S1]["xm"][2], mins[S1])
    check("build: blank gameweek is 0 minutes, the next one is not",
          all(m["xm"][1] == 0 for m in mins.values()) and mins[S1]["xm"][2] > 60, mins[S1])
    check("build: injured player is out", mins[INJ]["ps"] == 0 and mins[INJ]["xm"][0] == 0, mins[INJ])
    side = sum(m["ps"] for p, m in mins.items() if by_id[p]["team"] == 1)
    check("build: a side's starters add up to eleven", 10.9 <= side <= 11.1, side)
    check("build: a starter outranks a benchwarmer",
          mins[pid(1, 9)]["ps"] > mins[pid(1, 13)]["ps"], (mins[pid(1, 9)], mins[pid(1, 13)]))
    bench = lambda t: sum(mins[e["id"]]["ps"] for e in elements
                          if e["team"] == t and not e["_starter"] and e["element_type"] != 1
                          and e["status"] == "a")
    check("build: an injured starter's share goes to his side's bench",
          bench(1) > bench(2) + 0.5, (bench(1), bench(2)))
    check("build: a doubtful keeper with no fit cover stays at FPL's 25%",
          mins[KEEPER]["ps"] <= 0.25 and mins[COVER]["ps"] == 0, (mins[KEEPER], mins[COVER]))
    check("shown: rounds to two places, never 1.00",
          update.shown(0.9959) == 0.99 and update.shown(0.5049) == 0.5 and update.shown(1.0) == 0.99)
    check("build: nothing published as a certainty",
          all(m.get(k, 0) <= 0.99 for m in mins.values() for k in ("ps", "p60", "pa")))

    # with the fixture model supplied, expected points come out too
    model = {"matchesPlayed": 1, "fit": {"kAtk": 8, "kDef": 35, "home": 1.1, "pen": 1.06},
             "teams": {"1": {"pa": 1.9, "pd": 0.9, "a26": 2.0, "d26": 0.8},
                       "2": {"pa": 1.1, "pd": 1.9, "a26": 1.0, "d26": 2.0}}}
    mp, _ = update.build_minutes(boot, raw_fx, 2, 2026, today=datetime.date(2026, 8, 22), fetch=fetch,
                                 model=model, market={2: {"1": 2.5}})
    check("build: expected points for every gameweek in the window",
          all(len(m["xp"]) == len(m["xm"]) for m in mp.values()))
    check("build: expected minutes unchanged by the points model",
          all(mp[k]["xm"] == mins[k]["xm"] for k in mins))
    check("build: a blank gameweek is 0 points", all(m["xp"][1] == 0 for m in mp.values()))
    check("build: a double gameweek is worth more than a single",
          mp[S1]["xp"][0] > 1.4 * mp[S1]["xp"][2], mp[S1])
    check("build: the injured player projects nothing", mp[INJ]["xp"][0] == 0, mp[INJ])
    check("build: the stronger side's starter outscores the weaker side's",
          mp[pid(1, 9)]["xp"][2] > mp[pid(2, 9)]["xp"][2], (mp[pid(1, 9)], mp[pid(2, 9)]))
    check("build: no points model unless asked", all("xp" not in m for m in mins.values()))

    kw = dict(today=datetime.date(2026, 8, 22), fetch=fetch, model=model)
    base_pts, _ = update.build_minutes(boot, raw_fx, 2, 2026, **kw)
    # the bookmakers' number: used for a single game, never for a double,
    # where one number a side cannot say which game it priced
    mq, _ = update.build_minutes(boot, raw_fx, 2, 2026, market={4: {"1": 3.5, "2": 0.3}}, **kw)
    check("build: a market price moves a single fixture",
          mq[S1]["xp"][2] > base_pts[S1]["xp"][2] + 0.3, (mq[S1], base_pts[S1]))
    md, _ = update.build_minutes(boot, raw_fx, 2, 2026, market={2: {"1": 3.5, "2": 0.3}}, **kw)
    check("build: a double gameweek ignores the market", md[S1]["xp"] == base_pts[S1]["xp"], (md[S1], base_pts[S1]))

    # this season's record, from FPL's live data: a starter with a lot of xG
    import copy as _cp
    live_hot = _cp.deepcopy(live_el)
    for e in live_hot:
        if e["id"] == S1:
            e["stats"].update(expected_goals="1.50", minutes=90)
    mh, _ = update.build_minutes(boot, raw_fx, 2, 2026, today=datetime.date(2026, 8, 22),
                                 fetch=lambda url: {"elements": live_hot}, model=model)
    check("build: this season's live record is used", mh[S1]["xp"][2] > base_pts[S1]["xp"][2] + 0.3, (mh[S1], base_pts[S1]))

    # a failure in the points model costs the points, not the minutes
    real_expected = update.PT.expected
    for bad in (lambda *a, **k: 1 / 0, lambda *a, **k: {"total": 99.0}):
        update.PT.expected = bad
        try:
            mf, _ = update.build_minutes(boot, raw_fx, 2, 2026, **kw)
        finally:
            update.PT.expected = real_expected
        check("build: a broken or out-of-range points model leaves the minutes intact",
              all("xp" not in m for m in mf.values()) and all(mf[k]["xm"] == mins[k]["xm"] for k in mins))

    # last season's record, from points_prior.json
    import json as _j, tempfile as _tf
    real_prior = update.PRIOR_PTS
    def with_prior(doc):
        with _tf.NamedTemporaryFile("w", suffix=".json", delete=False) as fp:
            _j.dump(doc, fp)
        update.PRIOR_PTS = pathlib.Path(fp.name)
        try:
            return update.build_minutes(boot, raw_fx, 2, 2026, **kw)[0]
        finally:
            update.PRIOR_PTS = real_prior
            pathlib.Path(fp.name).unlink()
    scorer = {str(9000 + S1): {"X": 12.0, "E": 30.0}}
    mpp = with_prior({"season": "2025/26", "lam": update.PT.LAM_P, "players": scorer})
    check("build: last season's record is used", mpp[S1]["xp"][2] > base_pts[S1]["xp"][2] + 0.3, (mpp[S1], base_pts[S1]))
    check("build: ...and only for that player", mpp[pid(1, 4)]["xp"] == base_pts[pid(1, 4)]["xp"])
    check("build: a prior for the wrong season is ignored",
          with_prior({"season": "2024/25", "lam": update.PT.LAM_P, "players": scorer})[S1]["xp"] == base_pts[S1]["xp"])
    check("build: a prior built with another decay is ignored",
          with_prior({"season": "2025/26", "lam": 0.5, "players": scorer})[S1]["xp"] == base_pts[S1]["xp"])

    # market.json -> {gameweek (int): {team id (str): xG}}, the shape side_goals reads
    real_m = update.OUT_M
    with _tf.NamedTemporaryFile("w", suffix=".json", delete=False) as fm:
        _j.dump({"gw": {"4": {"AAA": 1.7, "ZZZ": 9.9}}}, fm)
    update.OUT_M = pathlib.Path(fm.name)
    try:
        mk = update.market_for_points({"1": {"short": "AAA"}, "2": {"short": "BBB"}})
    finally:
        update.OUT_M = real_m
        pathlib.Path(fm.name).unlink()
    check("market: gameweeks as numbers, teams as ids, unknown teams dropped", mk == {4: {"1": 1.7}}, mk)

    bad = list(live_el)
    bad[pid(1, 13) - 1] = el(pid(1, 13), 1, (1, 90))    # a 12th starter for side 1
    try:
        update.build_minutes(boot, raw_fx, 2, 2026, today=datetime.date(2026, 8, 22),
                             fetch=lambda url: {"elements": bad})
        check("build: refuses 23 starters in a fixture", False)
    except ValueError:
        check("build: refuses 23 starters in a fixture", True)

    # A reply listing only the players who played (no 0-minute entries) has
    # all 22 starts, so every other check passes -- but every unused sub
    # would silently lose his "did not play" rows. It must be refused.
    played_only = [x for x, e in zip(live_el, elements) if e["_starter"]]
    try:
        update.build_minutes(boot, raw_fx, 2, 2026, today=datetime.date(2026, 8, 22),
                             fetch=lambda url: {"elements": played_only})
        check("build: refuses a reply that leaves out the players who did not play", False)
    except ValueError:
        check("build: refuses a reply that leaves out the players who did not play", True)

    nostart = [el(e["id"], 0, (1, 0)) for e in elements]
    try:
        update.build_minutes(boot, raw_fx, 2, 2026, today=datetime.date(2026, 8, 22),
                             fetch=lambda url: {"elements": nostart})
        check("build: refuses a fixture with no starters", False)
    except ValueError:
        check("build: refuses a fixture with no starters", True)

    # A double gameweek already played. Player A started game 10, came off
    # after 20 minutes, then played 70 off the bench in game 11; player B
    # started game 11 only. A's start is credited to the longer game, so
    # game 10 counts 21 starters and game 11 counts 23 -- the gameweek total
    # is still exact, and the build must carry on.
    A_, B_ = pid(1, 2), pid(1, 13)
    dgw = []
    for e in squad():
        s1 = e["_starter"]
        if e["id"] == A_:
            dgw.append(el(A_, 1, (10, 20), (11, 70)))
        elif e["id"] == B_:
            dgw.append(el(B_, 1, (10, 0), (11, 90)))
        else:
            dgw.append(el(e["id"], 2 if s1 else 0, (10, 90 if s1 else 0), (11, 90 if s1 else 0)))
    fx2 = [fx(10, 1, 1, 2, "08-20", True), fx(11, 1, 2, 1, "08-23", True), fx(12, 2, 1, 2, "08-30", False)]
    fr2 = MN.live_fixture_rows({1: {"elements": dgw}}, {10: (1, "a"), 11: (1, "b")})
    c2 = collections.Counter(f for _, f, _, st, _ in fr2 if st)
    check("build: (the scenario does give 21 and 23)", c2 == {10: 21, 11: 23}, c2)
    try:
        m2, _ = update.build_minutes({"elements": squad()}, fx2, 2, 2026,
                                     today=datetime.date(2026, 8, 25), fetch=lambda url: {"elements": dgw})
        check("build: a start credited to the wrong game of a double is tolerated", len(m2) == 44)
    except ValueError as e:
        check("build: a start credited to the wrong game of a double is tolerated", False, e)

    # A double gameweek still being played: game 20 finished, game 21 live.
    # FPL's starts already count game 21, so player A (30 minutes off the
    # bench in game 20, starting game 21) is credited with a start in game
    # 20 -- 23 starters there. The exact check must wait for the gameweek.
    live = []
    for e in squad():
        s1 = e["_starter"]
        if e["id"] == A_:
            live.append(el(A_, 1, (20, 30), (21, 10)))
        elif e["id"] == B_:
            live.append(el(B_, 1, (20, 90), (21, 0)))
        else:
            live.append(el(e["id"], 2 if s1 else 0, (20, 90 if s1 else 0), (21, 10 if s1 else 0)))
    fx3 = [fx(20, 1, 1, 2, "08-20", True), fx(21, 1, 2, 1, "08-23", False), fx(22, 2, 1, 2, "08-30", False)]
    try:
        m3, _ = update.build_minutes({"elements": squad()}, fx3, 2, 2026,
                                     today=datetime.date(2026, 8, 23), fetch=lambda url: {"elements": live})
        check("build: a double still in progress does not trip the exact check", len(m3) == 44)
    except ValueError as e:
        check("build: a double still in progress does not trip the exact check", False, e)

    # keeping yesterday's figures when the model fails
    import json as _json, tempfile
    now = datetime.datetime(2026, 8, 22, 12, tzinfo=datetime.timezone.utc)
    prev = {"season": "2026/27", "generated": "2026-08-22T06:00:00+00:00",
            "minutes": {"gws": [2, 3, 4], "built": "2026-08-22T06:00:00+00:00"},
            "players": [{"i": 1, "xm": [80, 80, 80], "xp": [4.1, 3.2, 5.0], "ps": 0.9, "p60": 0.85, "pa": 0.95}]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        _json.dump(prev, fh)

    def keep(next_gw, season="2026/27", at=now):
        pl = [{"i": 1}, {"i": 2}]; pd = {"season": season}
        return update.keep_minutes(fh.name, pl, pd, next_gw, at), pl, pd

    n, pl, pd = keep(2)
    check("keep: same gameweeks, same season, fresh -> kept and marked stale",
          n == 1 and pl[0]["xm"] == [80, 80, 80] and "xm" not in pl[1] and pd["minutes"]["stale"], (n, pl, pd))
    check("keep: expected points are carried with the minutes", pl[0].get("xp") == [4.1, 3.2, 5.0], pl[0])
    check("keep: the window has moved on -> dropped", keep(3)[0] == 0)
    check("keep: another season -> dropped", keep(2, "2027/28")[0] == 0)
    check("keep: over three days old -> dropped", keep(2, at=now + datetime.timedelta(days=4))[0] == 0)
    check("keep: no previous file -> dropped",
          update.keep_minutes("/nonexistent/players.json", [{"i": 1}], {"season": "2026/27"}, 2, now) == 0)
    pathlib.Path(fh.name).unlink()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
