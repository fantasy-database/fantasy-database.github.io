"""
Checks on how update.py copes when Understat cannot be reached: the team
ratings and the players' xG are carried over from the last good build, and
everything FPL provides still refreshes around them. Only with no earlier build
at all does a run fail -- and then before any odds are paid for.

No network. Run: python understat_outage_test.py
"""
import json, pathlib, sys, tempfile, urllib.error

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
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


logs = []
U.log = lambda *a, **k: logs.append(" ".join(map(str, a)))
U.time.sleep = lambda s: None                       # no real waiting in the retries
tmp = pathlib.Path(tempfile.mkdtemp())


def down(*a, **k):
    raise urllib.error.URLError("[Errno -3] Temporary failure in name resolution")


# --- reachability: DNS failure is "down", any HTTP answer is "up" -----------
U.urllib.request.urlopen = down
check("DNS failure means unreachable", U.understat_reachable() is False)
check("it tries three times", sum("try" in l for l in logs) == 3, logs)


def http_error(*a, **k):
    raise urllib.error.HTTPError(U.UND, 503, "busy", {}, None)


U.urllib.request.urlopen = http_error
check("an HTTP error still counts as reachable", U.understat_reachable() is True)

# --- ratings_frozen: last build's ratings, today's FPL around them -----------
previous = {
    "generated": "2026-11-01T21:12:00+00:00", "matchesPlayed": 8,
    "fit": {"kAtk": 24, "kDef": 35, "home": 1.11, "pen": 1.07, "level": 0.95},
    "teams": {"1": {"name": "Arsenal", "short": "ars", "pa": 1.9, "pd": 0.9, "a26": 2.0, "d26": 0.8},
              "2": {"name": "Hull", "short": "hul", "pa": 1.1, "pd": 1.9, "a26": 1.0, "d26": 2.0,
                    "promoted": 1}},
}
short2id = {"ARS": 1, "HUL": 2}
meta = {1: {"name": "Arsenal FC", "short": "ars", "code": 3},   # FPL's fresh names win
        2: {"name": "Hull City", "short": "hul", "code": 88}}
raw = [{"team_h": 1, "team_a": 2, "finished": True}] * 9 + [{"team_h": 1, "team_a": 2, "finished": False}]
r = U.ratings_frozen(previous, short2id, meta, raw)
check("ratings carried over unchanged",
      all(r["teams"][i][k] == previous["teams"][i][k] for i in ("1", "2") for k in ("pa", "pd", "a26", "d26")))
check("promoted flag carried over", r["teams"]["2"].get("promoted") == 1 and "promoted" not in r["teams"]["1"])
check("names and codes come from FPL", r["teams"]["1"]["name"] == "Arsenal FC" and r["teams"]["2"]["code"] == 88)
check("fitted constants carried over",
      (r["hfac"], r["kDef"], r["pen"]) == (1.11, 35, 1.07), r)
check("matches played counted from FPL's finished fixtures", r["played"] == 9, r["played"])
check("matches played falls back to the last build without FPL",
      U.ratings_frozen(previous, short2id, meta, None)["played"] == 8)
try:
    U.ratings_frozen(previous, {"ARS": 1, "NEW": 9}, {**meta, 9: {"name": "Newcastle", "short": "new"}}, raw)
    check("a side with no earlier rating is refused", False)
except RuntimeError as e:
    check("a side with no earlier rating is refused", "NEW" in str(e), e)

# --- carry_understat_fields: xG back onto each player, by FPL id -------------
old_p = tmp / "players.json"
old_p.write_text(json.dumps({"players": [
    {"i": 10, "npxg": 3.2, "xa": 1.1, "npxgi": 4.3, "sh": 20, "npg": 3,
     "npxg90": 0.4, "xa90": 0.12, "npxgi90": 0.52, "sh90": 2.5, "npg90": 0.38, "st": "i"}]}))
fresh = [{"i": 10, "npxg": None, "xa": None, "st": "a"}, {"i": 11, "npxg": None}]
n = U.carry_understat_fields(fresh, old_p)
check("xG carried for the player who was there before", n == 1 and fresh[0]["npxg"] == 3.2
      and fresh[0]["npg90"] == 0.38 and fresh[0]["sh"] == 20, fresh[0])
check("FPL's own fields are not overwritten", fresh[0]["st"] == "a")
check("a new player keeps his nulls", fresh[1]["npxg"] is None)
check("no earlier players.json: nothing carried, no crash",
      U.carry_understat_fields([{"i": 1}], tmp / "missing.json") == 0)

# --- main(): with nothing to carry over, stop before any odds are paid for ---
U.urllib.request.urlopen = down
U.OUT = tmp / "data.json"                       # does not exist
U.plan_run = lambda state: (True, True, "test: build, with odds")
touched = []
U.load_fpl = lambda prev: touched.append("load_fpl") or {}
U._write_state = lambda st: touched.append("state")
try:
    U.main(); raised = None
except RuntimeError as e:
    raised = e
check("no earlier data.json: main() fails", raised is not None and "no earlier data.json" in str(raised), raised)
check("no earlier data.json: nothing fetched, no odds spent", touched == [], touched)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
