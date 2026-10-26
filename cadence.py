"""
When to rebuild, and when to spend odds credits.

The workflow wakes every hour. Most wake-ups should do nothing at all; this file
decides, from what is already in the repo: data.json's deadline calendar,
market.json's last good fetch, and cadence.json -- a small state file update.py
writes the moment it asks the odds API anything, so a paid fetch is on record
even if the rest of the build then fails.

Free data (FPL prices, injuries, fixtures, Understat xG) costs nothing to pull,
so the site rebuilds every two hours, and every hour in the six hours before a
deadline, when team news is landing.

Odds cost 4 credits a fetch (2 regions x 2 markets, odds.fetch) out of 500 a
month on the free plan, so they are rationed:

  late       once 15 min - 1.5h before each deadline: the last prices before
             the cut-off, team news priced in
  early      once 1.5 - 3h before each deadline, a fallback in case GitHub
             starts the late run too late (it has started runs hours late)
  day-before once 20-26h before each deadline
  routine    otherwise every 6.5h, while more than RESERVE credits remain

On hourly wake-ups, 6.5h comes out as one routine fetch every 7h. Simulated
over November and December 2026 (cadence_test.py) that is about 440-460
credits a 30-day window -- most of the 500, a little left for hand-started
runs. RESERVE keeps the last credits of a billing month for the deadline
fetches.

The balance comes from odds.credits_left(), read free of charge every wake-up,
so the rationing sees the monthly reset (on an unknown day) the hour it lands.
If that read keeps failing, the last known balance is trusted for CREDIT_TTL
hours and then treated as unknown, so the rationing lifts rather than blocking
odds for good; an out-of-credit request is refused rather than charged.
"""

import datetime

COST = 4                 # credits per odds fetch
RESERVE = 40             # at or below this, only deadline fetches
CREDIT_TTL = 48          # hours a credit balance is trusted for
ROUTINE_HOURS = 6.5      # minimum gap between routine fetches
BUILD_HOURS = 1.4        # rebuild once the last build is this old (2h wake-ups,
                         # with room for GitHub starting runs late)
BUSY_HOURS = 6           # within this many hours of a deadline, rebuild every wake-up
LATE = (0.25, 1.5)       # hours before the deadline
EARLY = (1.5, 3.0)
DAY_BEFORE = (20, 26)

UTC = datetime.timezone.utc


def parse(ts):
    """ISO time -> aware UTC datetime, or None for anything unusable.

    A bare date (only if a data.json predates deadlineTimes) is read as 11:00
    UTC, the earliest usual deadline, so its windows fall before the cut-off."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        if len(ts) <= 10:
            ts += "T11:00:00"
        t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def as_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def next_deadline(deadline_times, now):
    for ts in deadline_times if isinstance(deadline_times, list) else []:
        t = parse(ts)
        if t and t > now:
            return t
    return None


def latest(*times):
    times = [t for t in times if t]
    return max(times) if times else None


def decide(now, data, market, state=None, mode="auto", manual=False):
    """
    Returns (build, odds, reason).

    data, market, state: data.json, market.json and cadence.json as dicts
    ({} if absent). mode: "auto" (the rules above), "yes" (fetch odds
    regardless), "no" (never). manual: a hand-started run, always rebuilds.
    """
    data = data if isinstance(data, dict) else {}
    market = market if isinstance(market, dict) else {}
    state = state if isinstance(state, dict) else {}
    hours = lambda a, b: (b - a).total_seconds() / 3600

    deadline = next_deadline(data.get("deadlineTimes") or data.get("deadlines"), now)
    to_dl = hours(now, deadline) if deadline else None
    last_build = latest(parse(state.get("lastBuild")), parse(data.get("generated")))
    last_odds = latest(parse(state.get("oddsAttempt")), parse(market.get("generated")))

    # The newest credit reading, from either file, if it is recent enough to trust.
    readings = [(parse(state.get("creditsAt")), as_int(state.get("credits"))),
                (parse(market.get("generated")), as_int(market.get("creditsRemaining")))]
    readings = [r for r in readings if r[0] and r[1] is not None]
    credits = None
    if readings:
        at, n = max(readings, key=lambda r: r[0])
        if hours(at, now) <= CREDIT_TTL:
            credits = n

    def fresh_since(hours_before):
        """No fetch since this many hours before the deadline."""
        return last_odds is None or hours(last_odds, deadline) > hours_before

    # --- odds -----------------------------------------------------------------
    if mode == "no":
        odds, why = False, "odds turned off for this run"
    elif mode == "yes":
        odds, why = True, "odds asked for by hand"
    elif credits is not None and credits < COST:
        odds, why = False, f"only {credits} odds credits left"
    elif to_dl is not None and LATE[0] < to_dl <= LATE[1] and fresh_since(LATE[1]):
        odds, why = True, f"last prices before the deadline ({to_dl:.1f}h away)"
    elif to_dl is not None and EARLY[0] < to_dl <= EARLY[1] and fresh_since(EARLY[1]):
        odds, why = True, f"pre-deadline prices ({to_dl:.1f}h away)"
    elif to_dl is not None and DAY_BEFORE[0] < to_dl <= DAY_BEFORE[1] and fresh_since(DAY_BEFORE[1]):
        odds, why = True, f"day-before prices ({to_dl:.1f}h before the deadline)"
    elif last_odds is not None and hours(last_odds, now) < ROUTINE_HOURS:
        odds, why = False, f"odds fetched {hours(last_odds, now):.1f}h ago"
    elif credits is not None and credits <= RESERVE:
        odds, why = False, f"{credits} credits left, kept for deadline fetches"
    elif last_odds is not None and last_build is not None and last_odds > last_build:
        # The last fetch never made it into a published build: while builds keep
        # failing, routine fetches would be paid for and thrown away.
        odds, why = False, "last odds fetch was not published (build failing?)"
    else:
        odds, why = True, "routine odds refresh"

    # --- the rest of the build ---------------------------------------------------
    if odds or manual or last_build is None:
        build = True
    elif to_dl is not None and to_dl <= BUSY_HOURS:
        build = True
    else:
        build = hours(last_build, now) >= BUILD_HOURS
    if not build:
        why = f"built {hours(last_build, now):.1f}h ago; {why}"
    return build, odds, why
