"""
Checks on Elevenify's projections (6 Nov 2026): reading the sheet's CSV,
pairing its team x gameweek cells with FPL's fixtures, the ratings solved
from them, the blend in points.py, and update.py's elevenify_step(), which
must never stop a build.

No network. Run: python elevenify_test.py
"""
import copy, datetime, io, json, math, pathlib, sys, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import elevenify as EL
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


U.log = lambda *a, **k: None

D = json.loads((HERE / "data.json").read_text())
TEAMS = D["teams"]
FIX = D["fixtures"]
NXT = D["nextGw"]
EL_NAME = {v: k for k, v in EL.NAMES.items()}
sheet_name = {tid: EL_NAME.get(t["name"], t["name"]) for tid, t in TEAMS.items()}

# A made-up truth: attack and defence per side, and goals = 1.35 * A * D' * home^(+-1).
ids = sorted(TEAMS, key=int)
TA = {tid: 0.6 + 0.07 * i for i, tid in enumerate(ids)}
TD = {tid: 1.5 - 0.05 * i for i, tid in enumerate(ids)}
H = D["fit"]["home"]


def make_csv(rearranged=None):
    """The sheet as Google's CSV export lays it out, built from the truth above."""
    goals = {tid: {} for tid in ids}
    for gw, h, a, *_ in FIX:
        h, a = str(h), str(a)
        if rearranged and (gw, h, a) == rearranged:
            continue
        goals[h][gw] = 1.35 * TA[h] * TD[a] * H
        goals[a][gw] = 1.35 * TA[a] * TD[h] / H
    opp = {}
    for gw, h, a, *_ in FIX:
        opp[(gw, str(h))], opp[(gw, str(a))] = str(a), str(h)
    if rearranged:   # the sheet still has the old fixture in that round
        gw, h, a = rearranged
        goals[h][gw], goals[a][gw] = 1.0, 1.0
    out = io.StringIO()
    w = lambda *cells: out.write(",".join(str(c) for c in cells) + "\n")
    w(",,,elevenify.com Season Long Live Projections")
    w(",,,Projected Goals")
    w("")
    w(",,Team," + ",".join(f"GW{g}" for g in range(1, 39)))
    for tid in ids:
        w(",," + sheet_name[tid] + "," + ",".join(f"{goals[tid].get(g, 0):.6f}" for g in range(1, 39)))
    w("")
    w(",,,Projected Clean Sheets")
    w("")
    w(",,Team," + ",".join(f"GW{g}" for g in range(1, 39)))
    for tid in ids:
        cs = [f"{math.exp(-goals[opp[(g, tid)]].get(g, 0)):.6f}" if (g, tid) in opp else "" for g in range(1, 39)]
        if rearranged and tid == rearranged[1]:   # its real opponent that round was someone else
            cs[rearranged[0] - 1] = f"{math.exp(-2.0):.6f}"
        w(",," + sheet_name[tid] + "," + ",".join(cs))
    return out.getvalue()


print("parse")
S = EL.parse(make_csv())
check("both sections, 20 sides each", len(S["goals"]) == 20 and len(S["cs"]) == 20)
check("38 gameweeks read", len(S["goals"]["Arsenal"]) == 38)
try:
    EL.parse(",,Team,GW1\n,,Arsenal,1.2\n")
    check("a sheet with no sections is refused", False)
except ValueError:
    check("a sheet with no sections is refused", True)

print("pairing")
last = NXT + EL.WINDOW - 1
hist, dropped = EL.fixtures_by_gw(S, TEAMS, FIX, NXT, last)
want = sum(1 for f in FIX if NXT <= f[0] <= last)
check("every fixture in the window kept", sum(len(v) for v in hist.values()) == want, (len(hist), want, dropped[:3]))
f0 = next(f for f in FIX if f[0] == NXT + 1)
moved = (f0[0], str(f0[1]), str(f0[2]))
h2, d2 = EL.fixtures_by_gw(EL.parse(make_csv(rearranged=moved)), TEAMS, FIX, NXT, last)
check("a fixture the sheet has differently is dropped",
      len(d2) == 1 and "different fixture" in d2[0], d2)
dbl = FIX + [[NXT + 2, f0[1], f0[2], 3, 3]]
h3, d3 = EL.fixtures_by_gw(S, TEAMS, dbl, NXT, last)
check("both games of a double gameweek are dropped", sum("double" in x for x in d3) == 3, d3)

print("ratings")
rat = O.implied_ratings(hist, H, last, window=EL.WINDOW, min_rounds=EL.MIN_ROUNDS)
short = {tid: TEAMS[tid]["short"] for tid in ids}
ma = sum(TA.values()) / 20
md = sum(TD.values()) / 20
err = max(max(abs(rat[short[t]]["mA"] - TA[t] / ma), abs(rat[short[t]]["mD"] - TD[t] / md)) for t in ids)
check("the solver gets the truth back", err < 1e-3, err)

print("blend")
teams = copy.deepcopy(TEAMS)
for t in teams.values():
    v = rat[t["short"]]
    t.update({"eA": v["mA"], "eD": v["mD"], "eN": v["mN"]})
fit = D["fit"]
base = PT.strengths(teams, D["matchesPlayed"], fit["kAtk"], fit["kDef"], 4, fit.get("kMarket"))
with_e = PT.strengths(teams, D["matchesPlayed"], fit["kAtk"], fit["kDef"], 4, fit.get("kMarket"), 20)
check("ke=None leaves the ratings as they were", base == PT.strengths(TEAMS, D["matchesPlayed"],
      fit["kAtk"], fit["kDef"], 4, fit.get("kMarket")))
t1 = ids[-1]   # strongest made-up attack
check("the blend moves a side toward Elevenify's view",
      abs(math.log(with_e["ATK"][t1]) - math.log(rat[short[t1]]["mA"]))
      < abs(math.log(base["ATK"][t1]) - math.log(rat[short[t1]]["mA"])))
check("ratings still average 1", abs(sum(with_e["ATK"].values()) / 20 - 1) < 1e-9)

print("elevenify_step")


class Reply(io.BytesIO):
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *a): return False


def run(reply, previous):
    real = urllib.request.urlopen
    def fake(req, timeout=None):
        if isinstance(reply, Exception):
            raise reply
        return Reply(reply.encode())
    urllib.request.urlopen = fake
    try:
        teams = copy.deepcopy(TEAMS)
        for t in teams.values():
            for k in ("eA", "eD", "eN"):
                t.pop(k, None)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        data = {"generated": now, "fit": dict(D["fit"]), "teams": teams}
        ok = U.elevenify_step(teams, FIX, NXT, H, data, previous)
        return ok, data
    finally:
        urllib.request.urlopen = real


ok, good = run(make_csv(), None)
check("a good sheet attaches eA/eD/eN to every side and sets kEleven",
      ok and all("eA" in t for t in good["teams"].values()) and good["fit"]["kEleven"] == U.ELEVENIFY_K)
prev = copy.deepcopy(good)
ok, d = run(OSError("down"), prev)
check("a failed read carries the last good ratings over",
      not ok and d["teams"] == prev["teams"] and d["fit"]["kEleven"] == U.ELEVENIFY_K)
stale = copy.deepcopy(prev)
stale["elevenify"]["read"] = "2026-10-01T00:00:00+00:00"
ok, d = run(OSError("down"), stale)
check("a stale earlier read is not used",
      not ok and not any("eA" in t for t in d["teams"].values()) and "kEleven" not in d["fit"])
ok, d = run("<html>We will come back soon</html>", None)
check("an HTML page instead of the sheet: no Elevenify, no exception",
      not ok and "kEleven" not in d["fit"])
U.ELEVENIFY_K = 0
ok, d = run(make_csv(), prev)
check("ELEVENIFY_K = 0 switches it off, earlier read or not",
      not ok and "kEleven" not in d["fit"] and not any("eA" in t for t in d["teams"].values()))
U.ELEVENIFY_K = 20
bad = make_csv()
lines = bad.split("\n"); cells = lines[4].split(","); cells[-1] = "-"; lines[4] = ",".join(cells)
check("a '-' cell is skipped, not fatal", len(EL.parse("\n".join(lines))["goals"]) == 20)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
