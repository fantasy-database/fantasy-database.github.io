"""
Checks on the market's view of each side (24 Oct 2026): the season's price
history, the ratings solved from it, the blend that leans the xG ratings
toward it, and update.py's market_step(), which must never stop a build.

No network. Run: python market_test.py
"""
import copy, json, math, os, pathlib, shutil, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import odds as O
import points as PT
import update as U

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ok  ", name)
    else:
        failed += 1
        print("  FAIL", name, detail)


U.log = lambda *a, **k: None          # keep the output to the checks

# --- a small league whose market numbers are exactly multiplicative --------
TEAMS = ["aaa", "bbb", "ccc", "ddd", "eee", "fff"]
TRUE_A = {"aaa": 1.4, "bbb": 1.2, "ccc": 1.0, "ddd": 0.9, "eee": 0.8, "fff": 0.7}
TRUE_D = {"aaa": 0.7, "bbb": 0.85, "ccc": 1.0, "ddd": 1.05, "eee": 1.15, "fff": 1.25}
H, B = 1.1, 1.4
na, nd = sum(TRUE_A.values()) / 6, sum(TRUE_D.values()) / 6
TRUE_A = {t: v / na for t, v in TRUE_A.items()}
TRUE_D = {t: v / nd for t, v in TRUE_D.items()}


def price(h, a):
    return [h, a, B * TRUE_A[h] * TRUE_D[a] * H, B * TRUE_A[a] * TRUE_D[h] / H]


ROUNDS = {}
pairs = [(h, a) for h in TEAMS for a in TEAMS if h != a]
for i, (h, a) in enumerate(pairs):
    ROUNDS.setdefault(str(1 + i // 3), []).append(price(h, a))

print("\nPrice history")
hist0 = {"1": [["aaa", "bbb", 1.0, 1.0]], "2": [["ccc", "ddd", 1.2, 0.9]]}
snap = copy.deepcopy(hist0)
new = O.update_history(hist0, {"2": [["ccc", "ddd", 1.5, 0.7]], "3": [["eee", "fff", 1.1, 1.0]]}, 2)
check("a round still open takes the latest prices", new["2"] == [["ccc", "ddd", 1.5, 0.7]])
check("a new round is added", new["3"] == [["eee", "fff", 1.1, 1.0]])
part = O.update_history({"2": [["ccc", "ddd", 1.2, 0.9], ["eee", "fff", 1.0, 1.0]]}, {"2": [["ccc", "ddd", 1.5, 0.7]]}, 2)
check("a feed missing one game keeps that game's last price", part["2"] == [["ccc", "ddd", 1.5, 0.7], ["eee", "fff", 1.0, 1.0]])
new = O.update_history(hist0, {"1": [["aaa", "bbb", 9, 9]]}, 2)
check("a round whose deadline has passed is frozen", new["1"] == [["aaa", "bbb", 1.0, 1.0]])
check("the history passed in is never changed", hist0 == snap)
check("an empty history starts from nothing", O.update_history(None, {"5": [["a", "b", 1, 1]]}, 5) == {"5": [["a", "b", 1, 1]]})

print("\nRatings solved from the market")
r = O.implied_ratings(ROUNDS, H, 99, window=99)
check("every team rated", set(r) == set(TEAMS))
check("attack recovered exactly", all(abs(r[t]["mA"] - TRUE_A[t]) < 2e-4 for t in TEAMS),
      {t: (r[t]["mA"], round(TRUE_A[t], 4)) for t in TEAMS})
check("defence recovered exactly", all(abs(r[t]["mD"] - TRUE_D[t]) < 2e-4 for t in TEAMS))
check("mean attack is 1", abs(sum(v["mA"] for v in r.values()) / 6 - 1) < 1e-3)
check("mN counts each side's priced fixtures", all(r[t]["mN"] == 10 for t in TEAMS))
early = O.implied_ratings(ROUNDS, H, 4)
check("rounds after upto_gw are ignored", all(early[t]["mN"] < 10 for t in early))
check("fewer than three rounds says nothing", O.implied_ratings({g: ROUNDS[g] for g in ("1", "2")}, H, 99) == {})
win = O.implied_ratings(ROUNDS, H, 99)
check("only the last six rounds count", sum(v["mN"] for v in win.values()) == 2 * sum(len(ROUNDS[str(g)]) for g in range(5, 11)))
check("a window still recovers the true ratings", all(abs(win[t]["mA"] - TRUE_A[t]) < 2e-4 for t in win))

print("\nThe blend")
teams = {str(i): {"short": s, "pa": 1.4, "pd": 1.4, "a26": 1.4 + 0.1 * i, "d26": 1.5 - 0.1 * i}
         for i, s in enumerate(TEAMS)}
plain = PT.strengths(teams, 8, 24, 35)
for tid, t in teams.items():
    t.update(r[t["short"]])
check("no km leaves the ratings alone", PT.strengths(teams, 8, 24, 35) == plain)
check("km 0 leaves the ratings alone", PT.strengths(teams, 8, 24, 35, 4, 0) == plain)
bl = PT.strengths(teams, 8, 24, 35, 4, 5)
check("base is unchanged by the blend", bl["base"] == plain["base"])
check("blended attack still averages 1", abs(sum(bl["ATK"].values()) / 6 - 1) < 1e-12)
w = 10 / 15
raw = {t: math.exp((1 - w) * math.log(plain["ATK"][t]) + w * math.log(teams[t]["mA"])) for t in teams}
m = sum(raw.values()) / 6
check("weight is mN/(mN+km), in logs", all(abs(bl["ATK"][t] - raw[t] / m) < 1e-12 for t in teams))
big = PT.strengths(teams, 8, 24, 35, 4, 1e9)
check("a huge km is the xG ratings again", all(abs(big["ATK"][t] - plain["ATK"][t]) < 1e-6 for t in teams))

# Python and model.js must blend identically, on the real data.json
if shutil.which("node"):
    d = json.loads((HERE / "data.json").read_text())
    if d["fit"].get("kMarket"):
        f = d["fit"]
        py = PT.strengths(d["teams"], d["matchesPlayed"], f["kAtk"], f["kDef"], 4, f["kMarket"])
        js = json.loads(subprocess.check_output(["node", "-e", (
            'import("./model.js").then(M=>{const d=JSON.parse(require("fs").readFileSync("data.json"));'
            'const S=M.fromData(d).S;console.log(JSON.stringify({A:S.ATK,D:S.DEF}))})')], cwd=HERE))
        check("model.js blends exactly as points.py does (live data.json)",
              all(abs(py["ATK"][t] - js["A"][t]) < 1e-12 and abs(py["DEF"][t] - js["D"][t]) < 1e-12
                  for t in d["teams"]))
else:
    print("  (node not found - skipped the model.js parity check)")

print("\nmarket_step never stops a build")
os.environ["ODDS_API_KEY"] = "test"
FIX = [[int(g), None, None, 0, 0] for g in ROUNDS]           # filled below
id_of = {s: str(i) for i, s in enumerate(TEAMS)}
FIX = [[int(g), int(id_of[h]), int(id_of[a]), 3, 3] for g, rows in ROUNDS.items() for h, a, *_ in rows]
nxt = 9
priced_now = [{"home": h, "away": a, "home_xg": hx, "away_xg": ax} for h, a, hx, ax in ROUNDS[str(nxt)]]
prev = {"season": "2026/27", "history": {g: v for g, v in ROUNDS.items() if int(g) < nxt}}


def fresh():
    t = {str(i): {"short": s, "pa": 1.4, "pd": 1.4, "a26": 1.4, "d26": 1.4} for i, s in enumerate(TEAMS)}
    return t, {"season": "2026/27", "fit": {"kAtk": 24, "kDef": 35, "home": H, "pen": 1.06, "level": 0.95}}


t, d = fresh()
ok, by_gw, rem, hist = U.market_step(t, FIX, nxt, H, d, prev, lambda: (priced_now, "321"))
check("happy path: odds taken", ok and rem == "321" and set(by_gw) == {str(nxt)})
check("happy path: this round joins the history, prices to 3 dp",
      hist[str(nxt)] == [[h, a, round(hx, 3), round(ax, 3)] for h, a, hx, ax in ROUNDS[str(nxt)]])
check("happy path: every team gets market ratings", all("mA" in v and v["mN"] > 0 for v in t.values()))
check("happy path: kMarket is set", d["fit"].get("kMarket") == U.MARKET_K)


def boom():
    raise OSError("network down")


t, d = fresh()
moved = dict(prev, history=dict(prev["history"], **{str(nxt): [["aaa", "zzz", 1.0, 1.0]]}))
ok, by_gw, rem, hist = U.market_step(t, FIX, nxt, H, d, moved, lambda: (priced_now, "1"))
check("a game no longer in an open round loses its price there",
      all(r[1] != "zzz" for r in hist[str(nxt)]))

t, d = fresh()
ok, by_gw, rem, hist = U.market_step(t, FIX, nxt, H, d, prev, boom)
check("odds down: build carries on with last week's history", not ok and by_gw == {} and hist == prev["history"])
check("odds down: market ratings still applied from history", all("mA" in v for v in t.values()) and d["fit"].get("kMarket"))

t, d = fresh()
U.market_step(t, FIX, nxt, H, d, dict(prev, season="2025/26"), boom)
check("last season's history is not used in a new season",
      not any("mA" in v for v in t.values()) and "kMarket" not in d["fit"])

t, d = fresh()
ok, *_ = U.market_step(t, FIX, nxt, H, d, {"season": "2026/27", "history": {"1": [["aaa", "bbb", "junk", 1]]}}, boom)
check("corrupt history: no market ratings, no kMarket, no exception",
      not any("mA" in v for v in t.values()) and "kMarket" not in d["fit"])

t, d = fresh()
t["99"] = {"short": "zzz", "pa": 1, "pd": 1, "a26": 1, "d26": 1}
U.market_step(t, FIX, nxt, H, d, prev, boom)
check("a side the books have never priced: market ratings off for everyone",
      not any("mA" in v for v in t.values()) and "kMarket" not in d["fit"])

t, d = fresh()
del os.environ["ODDS_API_KEY"]
U.market_step(t, FIX, nxt, H, d, {}, boom)
check("no key and no history: plain xG ratings", not any("mA" in v for v in t.values()))
os.environ["ODDS_API_KEY"] = "test"

real = O.implied_ratings
U.odds_mod.implied_ratings = lambda *a, **k: {s: {"mA": 9.0, "mD": 1.0, "mN": 3} for s in TEAMS}
t, d = fresh()
U.market_step(t, FIX, nxt, H, d, prev, boom)
check("an absurd market rating drops the blend instead of stopping the build",
      not any("mA" in v for v in t.values()) and "kMarket" not in d["fit"])
U.odds_mod.implied_ratings = real

saved = U.MARKET_K
U.MARKET_K = 0
t, d = fresh()
U.market_step(t, FIX, nxt, H, d, prev, lambda: (priced_now, "1"))
check("MARKET_K = 0 switches it off", not any("mA" in v for v in t.values()) and "kMarket" not in d["fit"])
U.MARKET_K = saved

print("\nTwo scorecards")
t, d = fresh()
U.market_step(t, FIX, nxt, H, d, prev, lambda: (priced_now, "1"))
for i, v in t.items():
    v["a26"] = 1.4 + 0.05 * int(i)                             # give xG an opinion of its own
data = {"nextGw": nxt, "matchesPlayed": 8, "fit": d["fit"], "teams": t, "fixtures": FIX}
mk = {s: x for h, a, hx, ax in ROUNDS[str(nxt)] for s, x in ((h, hx), (a, ax))}
pure = U.score_against_market(data, mk)
blend = U.score_against_market(data, mk, blended=True)
noblend = copy.deepcopy(data)
for v in noblend["teams"].values():
    for k in ("mA", "mD", "mN"):
        v.pop(k)
check("the xG-alone scorecard ignores the market ratings",
      pure["model"] == U.score_against_market(noblend, mk)["model"])
check("the blended scorecard is closer to the market it borrowed from",
      blend["scorecard"]["rmse"] < pure["scorecard"]["rmse"])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
