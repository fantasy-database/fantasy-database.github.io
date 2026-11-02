"""
Checks on update.main() when Understat cannot be reached: a short outage
stands down quietly (no odds spent, no files touched, no failure), a long one
fails loudly. No network. Run: python understat_outage_test.py
"""
import datetime, json, pathlib, sys, tempfile, urllib.error

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
U.OUT = tmp / "data.json"
U.plan_run = lambda state: (True, True, "test: build, with odds")
touched = []
U.load_fpl = lambda prev: touched.append("load_fpl") or (_ for _ in ()).throw(AssertionError("built anyway"))
U._write_state = lambda st: touched.append("state")
now = datetime.datetime.now(datetime.timezone.utc)


def write_data(hours_old):
    gen = (now - datetime.timedelta(hours=hours_old)).isoformat(timespec="seconds")
    U.OUT.write_text(json.dumps({"generated": gen}))


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

# --- a short outage: stand down, touch nothing --------------------------------
U.urllib.request.urlopen = down
write_data(3)
logs.clear(); touched.clear()
try:
    U.main(); ok = True
except Exception as e:
    ok = False; err = e
check("fresh data: main() returns without error", ok, locals().get("err"))
check("fresh data: no build, no odds, no state written", touched == [], touched)
check("fresh data: a warning annotation, not an error",
      any(l.startswith("::warning") and "stands down" in l for l in logs), logs[-1:])

# --- a long outage: fail loudly -----------------------------------------------
write_data(30)
touched.clear()
try:
    U.main(); raised = None
except RuntimeError as e:
    raised = e
check("stale data: main() fails", raised is not None and "30h old" in str(raised), raised)
check("stale data: still no odds spent", touched == [], touched)

U.OUT.unlink()
try:
    U.main(); raised = None
except RuntimeError as e:
    raised = e
check("no data file: main() fails", raised is not None and "missing" in str(raised), raised)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
