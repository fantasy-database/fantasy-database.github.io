"""
Checks cadence.py: the deadline windows fire once each, a simulated month of
hourly wake-ups (started late, the way GitHub starts them) stays inside the free
plan's 500 odds credits, and the failure cases do not drain credits or stall.

    python cadence_test.py
"""
import datetime
import random
import cadence as C

UTC = datetime.timezone.utc
H = lambda h: datetime.timedelta(hours=h)
T = lambda s: datetime.datetime.fromisoformat(s).replace(tzinfo=UTC)
iso = lambda d: d.isoformat(timespec="seconds")

# The real 2026/27 deadlines from GW9 into the January rush.
DEADLINES = ["2026-10-31T11:00:00Z", "2026-11-06T18:30:00Z", "2026-11-21T11:00:00Z",
             "2026-11-27T18:30:00Z", "2026-12-01T18:30:00Z", "2026-12-05T11:00:00Z",
             "2026-12-12T13:30:00Z", "2026-12-19T11:00:00Z", "2026-12-26T11:00:00Z",
             "2026-12-29T18:30:00Z", "2027-01-01T11:00:00Z", "2027-01-05T18:30:00Z"]


def simulate(start, days, credits=500, lag=lambda: 17, build_ok=True, priced_ok=True,
             charge=True, reset_at=None, probe=True):
    """Hourly wake-ups, each started `lag()` minutes past the hour. Mirrors
    update.py: the free balance read (`probe`) before deciding; cadence.json
    written before each request and after each build; market.json only when
    the fetch and the build both succeed."""
    now, end = T(start), T(start) + H(24 * days)
    data = {"generated": iso(now - H(3)), "deadlineTimes": DEADLINES}
    market = {"generated": iso(now - H(9)), "creditsRemaining": str(credits)}
    state, balance = {}, credits
    fetches, builds = [], 0
    while now < end:
        if reset_at and now >= T(reset_at):
            balance, reset_at = 500, None
        t = now + datetime.timedelta(minutes=lag())
        if probe:
            state.update(credits=balance, creditsAt=iso(t))
        build, odds, why = C.decide(t, data, market, state)
        if odds:
            state["oddsAttempt"] = iso(t)
            if balance >= C.COST or not charge:        # out of credits: refused, not charged
                if charge:
                    balance -= C.COST
                state.update(credits=balance, creditsAt=iso(t))
                fetches.append((t, why))
                ok = priced_ok and build_ok
                if ok:
                    market = {"generated": iso(t), "creditsRemaining": str(balance)}
        if build:
            builds += 1
            if build_ok:
                data["generated"] = iso(t)
                state["lastBuild"] = iso(t)
        now += H(1)
    return fetches, builds, balance


def window_max(fetches, days=30):
    times = [t for t, _ in fetches]
    return max(sum(1 for u in times if t <= u < t + H(24 * days)) for t in times) * C.COST


def test_budget_with_late_starts():
    random.seed(1)
    for start in ("2026-11-01T00:00:00", "2026-12-01T00:00:00"):
        f, builds, left = simulate(start, 30, lag=lambda: random.randint(5, 60))
        used = 500 - left
        assert left >= 0 and 400 <= used <= 480, f"used {used} credits from {start}"
        assert 300 <= builds <= 460, f"{builds} builds from {start}"
        print(f"  {start[:10]} +30d, random 5-60 min late: {len(f)} fetches, {used} credits, {builds} builds")
    f, _, _ = simulate("2026-10-30T00:00:00", 70, credits=10**6)
    m = window_max(f)
    assert m <= 480, f"{m} credits in the busiest 30 days"
    print(f"  busiest 30 days, Oct-Jan: {m} credits")


def test_every_deadline_gets_its_windows():
    f, _, _ = simulate("2026-10-30T00:00:00", 66, credits=10**6)
    for ts in DEADLINES[:11]:
        d = C.parse(ts)
        before = lambda lo, hi: [t for t, _ in f if lo < (d - t).total_seconds() / 3600 <= hi]
        assert len(before(*C.LATE)) == 1, f"{ts}: {len(before(*C.LATE))} late fetches"
        assert len(before(*C.EARLY)) == 1, f"{ts}: {len(before(*C.EARLY))} early fetches"
        assert len(before(*C.DAY_BEFORE)) == 1, f"{ts}: {len(before(*C.DAY_BEFORE))} day-before"


def test_very_late_starts_still_get_a_pre_deadline_price():
    # GitHub has started a scheduled run five hours late; with 55-minute delays
    # every deadline must still get a fetch in its last three hours.
    f, _, _ = simulate("2026-10-30T00:00:00", 40, credits=10**6, lag=lambda: 55)
    for ts in DEADLINES[:6]:
        d = C.parse(ts)
        assert any(0 < (d - t).total_seconds() / 3600 <= 3 for t, _ in f), ts


def test_failed_builds_do_not_drain_credits():
    # The reviewer's case: the API charges, then the build fails, so
    # market.json never moves. cadence.json must stop the hourly re-buy.
    # Build failing: only deadline fetches go on (2 deadlines x 3 windows).
    f, _, left = simulate("2026-11-08T00:00:00", 10, build_ok=False)
    assert 500 - left <= 2 * 3 * C.COST + C.COST, f"failing build: {500 - left} credits"
    print(f"  build failing: {len(f)} fetches in 10 days")
    # Feed empty but the build fine: the normal ~7h rhythm, never hourly.
    f, _, left = simulate("2026-11-08T00:00:00", 10, priced_ok=False)
    assert 500 - left <= (10 * 24 / C.ROUTINE_HOURS + 2 * 3) * C.COST, f"empty feed: {500 - left} credits"
    print(f"  empty odds feed: {len(f)} fetches in 10 days")


def test_out_of_credits_recovers_after_reset():
    # Balance hits 0; the plan resets on the 15th. Odds must come back --
    # straight away with the free balance read, within days without it.
    for probe, by in ((True, "2026-11-15T02:00:00"), (False, "2026-11-18T00:00:00")):
        f, _, _ = simulate("2026-11-08T00:00:00", 14, credits=0,
                           reset_at="2026-11-15T00:00:00", probe=probe)
        assert not [t for t, _ in f if t < T("2026-11-15T00:00:00")], "fetched with no credits"
        after = [t for t, _ in f if t >= T("2026-11-15T00:00:00")]
        assert after and after[0] <= T(by), f"probe={probe}: resumed {after[:1]}"
        print(f"  0 credits, reset on the 15th, balance read {'on' if probe else 'off'}: "
              f"resumed {after[0]:%d %b %H:%M}")


def test_reserve_keeps_deadline_fetches():
    f, _, left = simulate("2026-11-01T00:00:00", 30, credits=60)
    assert left >= 0
    routine = [w for _, w in f if "routine" in w]
    assert len(routine) <= (60 - C.RESERVE) // C.COST + 1, f"{len(routine)} routine fetches"
    assert any("last prices" in w for _, w in f), "no late fetches once credits ran low"
    print(f"  starting on 60 credits: {len(f)} fetches ({len(routine)} routine), {left} left")


def test_modes_and_bad_input():
    now = T("2026-11-02T10:17:00")
    data = {"generated": iso(now - H(0.5)), "deadlineTimes": DEADLINES}
    fresh = {"generated": iso(now - H(1)), "creditsRemaining": "300"}
    stale = {"generated": iso(now - H(9)), "creditsRemaining": "300"}
    assert C.decide(now, data, fresh)[:2] == (False, False)
    assert C.decide(now, data, fresh, manual=True)[:2] == (True, False)
    assert C.decide(now, data, fresh, mode="yes")[:2] == (True, True)
    assert C.decide(now, data, stale, mode="no")[:2] == (False, False)
    assert C.decide(now, data, stale)[:2] == (True, True)
    assert C.decide(now, {}, {})[:2] == (True, True)                    # first ever run
    assert C.decide(now, data, dict(stale, creditsRemaining="3"))[1] is False
    assert C.decide(now, data, dict(stale, creditsRemaining="3.0"))[1] is False
    # A recent attempt in cadence.json counts even though market.json is old.
    assert C.decide(now, data, stale, {"oddsAttempt": iso(now - H(1))})[1] is False
    # Junk never raises.
    for junk in ({"generated": 5}, {"generated": "2026-11-02T08:00:00"}, [], None,
                 {"deadlineTimes": "x"}, {"deadlineTimes": [None, 3, "bad"]}):
        C.decide(now, junk, junk, junk)
    assert C.parse("2026-11-02T08:00:00") == T("2026-11-02T08:00:00")  # naive = UTC
    assert C.parse("2026-11-06") == T("2026-11-06T11:00:00")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
