"""
Minutes model: how likely a player is to start, to reach 60 minutes and to
appear at all in a fixture, and how many minutes to expect -- for the next
gameweek and for gameweeks further out.

Pure functions, no network, no dependencies. update.py feeds it FPL's
per-fixture history; the backtest (claude/backtest-minutes.md) feeds it the
same thing from past seasons, so the two cannot drift apart.

How it works
------------
Every fixture a player's team plays while he is registered adds one row:
did he start, and how many minutes did he play. Those rows are summarised
twice, as decayed counts:

  short memory  each older fixture worth half the one after it (LAM_S) --
                in practice the last two or three games
  long memory   each older fixture worth 0.98 of the next (LAM_L) -- in
                practice this whole season, plus last season at a quarter

Last season's rows carry into both, discounted by DELTA over the summer.

Each rate is the player's own decayed record shrunk towards a prior, the same
way the team ratings are:

    rate = (hits + k * prior) / (chances + k)

The start prior depends on position and price, because FPL's price is the
best guess there is about a player nobody has seen play yet.

The two memories are then blended, and the blend depends on how far ahead
the fixture is. Next week, what happened in the last few games is what
matters. Six weeks out, the short-term picture (a knock, a rested week, a
run of form) has mostly washed out and a player's longer record predicts
better. The weights W were fitted, not chosen -- see the backtest.

The model knows nothing about injuries, suspensions or transfers. FPL's news
is applied on top (availability()), and that part cannot be backtested
because FPL keeps no history of its news.
"""

import datetime, math, re

# ---- fitted constants (claude/backtest-minutes.md) -------------------------
# The five decay/shrinkage constants and W were tuned on 2024/25 (with 2023/24
# as its last season) and then scored, untouched, on 2025/26. Change them
# only with a rerun of the backtest.
LAM_S = 0.50
LAM_L = 0.98
DELTA = 0.25
K_S, K_Q, K_R = 0.5, 0.5, 0.5
# Weight on the short memory for a fixture h gameweeks ahead (h = 1 is next
# week). Beyond the end of the list the last value is used.
W = [0.80, 0.65, 0.55, 0.50, 0.45, 0.40, 0.40, 0.35]

# The pieces below are plain descriptions of a season rather than tuned
# choices, so they are refitted on the most recent full season (2025/26) --
# the same "fit on last season, forecast this one" arrangement the backtest
# scored. Refit each summer with the backtest's fit_priors() / fit_cal().

# Recalibration of P(start) per horizon, (a, b) on the log-odds scale.
CAL = [(-0.0422, 1.1434), (-0.1057, 1.0147), (-0.1489, 0.9342), (-0.1802, 0.882), (-0.2036, 0.8486), (-0.2228, 0.8247), (-0.2394, 0.8079), (-0.255, 0.791)]

# START_PRIOR is (a, b) of a logistic in price (GBP m):
#   P(start) = 1 / (1 + exp(-(a + b * price)))
START_PRIOR = {"GK": (-15.9575, 3.3464), "DEF": (-6.6805, 1.3296), "MID": (-5.0705, 0.7589), "FWD": (-5.1422, 0.6917)}
Q_PRIOR = {"GK": 0.9947, "DEF": 0.9442, "MID": 0.9059, "FWD": 0.9147}   # P(60+ | start)
R_PRIOR = {"GK": 0.0026, "DEF": 0.1164, "MID": 0.1816, "FWD": 0.2437}   # P(on | no start)
MS_PRIOR = {"GK": 89.66, "DEF": 85.22, "MID": 79.97, "FWD": 78.46}   # mins when starting
MB_PRIOR = {"GK": 36.43, "DEF": 17.64, "MID": 18.69, "FWD": 17.51}   # mins off the bench

POSITIONS = ("GK", "DEF", "MID", "FWD")
FPL_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def _p(p, name):
    """A constant, overridable by the backtest; update.py never overrides."""
    return p[name] if p and name in p else globals()[name]


# ---- one memory ------------------------------------------------------------
def empty():
    return {"n": 0.0, "s": 0.0, "s60": 0.0, "ms": 0.0, "b": 0.0, "mb": 0.0}


def step(st, started, minutes, lam):
    """Fold one team fixture into a memory. Returns a new memory."""
    started = 1.0 if started else 0.0
    minutes = float(minutes or 0)
    sub = 1.0 if (not started and minutes > 0) else 0.0
    return {
        "n": lam * st["n"] + 1.0,
        "s": lam * st["s"] + started,
        "s60": lam * st["s60"] + (1.0 if started and minutes >= 60 else 0.0),
        "ms": lam * st["ms"] + started * minutes,
        "b": lam * st["b"] + sub,
        "mb": lam * st["mb"] + sub * minutes,
    }


def carry(st, delta):
    return {k: v * delta for k, v in st.items()}


def memory(rows, lam, prior_rows=(), delta=None):
    """
    rows:       this season's fixtures, oldest first, as (started, minutes).
    prior_rows: last season's, the same shape, or empty.
    """
    st = empty()
    for s, m in prior_rows:
        st = step(st, s, m, lam)
    if prior_rows:
        st = carry(st, delta)
    for s, m in rows:
        st = step(st, s, m, lam)
    return st


# ---- rates from a memory ---------------------------------------------------
def start_prior(pos, price, table=None):
    a, b = (table or START_PRIOR)[pos]
    return 1.0 / (1.0 + math.exp(-(a + b * price)))


def rates(st, pos, price, p=None):
    """The four component rates for one memory, before availability."""
    ks, kq, kr = _p(p, "K_S"), _p(p, "K_Q"), _p(p, "K_R")
    mu_s = start_prior(pos, price, _p(p, "START_PRIOR"))
    ps = (st["s"] + ks * mu_s) / (st["n"] + ks)
    q = (st["s60"] + kq * _p(p, "Q_PRIOR")[pos]) / (st["s"] + kq)
    r = (st["b"] + kr * _p(p, "R_PRIOR")[pos]) / (max(st["n"] - st["s"], 0.0) + kr)
    ms = (st["ms"] + kq * _p(p, "MS_PRIOR")[pos]) / (st["s"] + kq)
    mb = (st["mb"] + kr * _p(p, "MB_PRIOR")[pos]) / (st["b"] + kr)
    return ps, q, r, ms, mb


def outcome(ps, q, r, ms, mb):
    """
    Component rates -> the numbers anything downstream wants. xs and xb are
    expected minutes from starting and from the bench; they add up to xmin
    and are kept apart so rescale() can move each correctly.
    """
    xs = ps * ms
    xb = (1.0 - ps) * r * mb
    return {"start": ps, "p60": ps * q, "app": ps + (1.0 - ps) * r,
            "xmin": xs + xb, "xs": xs, "xb": xb}


def weight(h, p=None):
    w = _p(p, "W")
    return w[min(max(h, 1), len(w)) - 1]


def blend(short, long_, h, p=None):
    """Mix two outcome dicts for a fixture h gameweeks ahead."""
    w = weight(h, p)
    return {k: w * short[k] + (1.0 - w) * long_[k] for k in short}


# ---- the whole thing -------------------------------------------------------
def fold(rows, prior_rows=(), p=None):
    """Both memories for one player."""
    d = _p(p, "DELTA")
    return {"S": memory(rows, _p(p, "LAM_S"), prior_rows, d),
            "L": memory(rows, _p(p, "LAM_L"), prior_rows, d)}


def _logit(x):
    return math.log(x / (1.0 - x))


def rescale(o, new):
    """
    Set P(start) to `new` and move everything that depends on it with it:
    the starting part of the outcome scales with P(start), the bench part
    with P(does not start). Keeps start <= app, p60 <= start and
    xmin = xs + xb true.
    """
    ps = o["start"]
    up = new / ps if ps > 1e-9 else 0.0
    down = (1.0 - new) / (1.0 - ps) if ps < 1 - 1e-9 else 1.0
    out = dict(o)
    out.update(start=new, p60=o["p60"] * up, app=new + (o["app"] - ps) * down,
               xs=o["xs"] * up, xb=o["xb"] * down, xmin=o["xs"] * up + o["xb"] * down)
    return out


def calibrate(o, h, p=None):
    """
    Re-map P(start) for a fixture h gameweeks ahead.

    The raw rates are honest about a player's record, but a record says
    nothing about the knock he picks up next month. Further out that matters
    more, and the raw numbers are too confident at the top end. CAL is a
    straight line on the log-odds scale per horizon.
    """
    cal = _p(p, "CAL")
    a, b = cal[min(max(h, 1), len(cal)) - 1]
    ps = o["start"]
    if ps <= 1e-9 or ps >= 1 - 1e-9:
        return dict(o)
    return rescale(o, 1.0 / (1.0 + math.exp(-(a + b * _logit(ps)))))


def forecast(mem, pos, price, h=1, avail=1.0, p=None):
    """
    One fixture h gameweeks ahead, before the line-up is filled (see
    fill_lineup). avail is the chance he is available at all
    (availability()); everything scales with it, because a player who is
    not there neither starts nor comes off the bench.

    Returns start, p60, app (probabilities), xmin (expected minutes), and
    xs / xb (the starting and bench parts of xmin).
    """
    s = outcome(*rates(mem["S"], pos, price, p))
    l = outcome(*rates(mem["L"], pos, price, p))
    o = calibrate(blend(s, l, h, p), h, p)
    return {k: v * avail for k, v in o.items()}


# ---- eleven start ----------------------------------------------------------
# Each player is forecast on his own record, so nothing yet stops a side
# being given ten starters, or thirteen. When a regular is ruled out his
# share has to go to somebody -- and on his own record the man who replaces
# him has often barely started. So for each side and fixture the chances of
# starting are shifted until they add up to one goalkeeper and ten outfield
# players.
#
# Outfield players are pooled rather than split into DEF / MID / FWD: FPL's
# labels are not the roles clubs play them in (a lone "FWD" is covered by a
# "MID", a wing-back by a winger), and splitting them forced a side's only
# listed forward to 100%. In the backtest the two ways scored the same.
#
# The shift is on the log-odds scale, and on the chance of starting GIVEN he
# is available: it moves a 40% player a lot and a 95% player hardly at all,
# and it can never lift a doubtful player above his availability -- a 25%
# keeper stays at 25% and his backup takes the rest. The shift is capped at
# MAX_SHIFT, which only bites when a side has fewer fit players than places:
# a backup keeper at 5% can still reach 90%, but a player with no record at
# all is not pushed to certainty. In the backtest any cap of 3 or more
# scored identically to no cap.
LINEUP = {"GK": 1.0, "OUT": 10.0}
MAX_SHIFT = 6.0


def group(pos):
    return "GK" if pos == "GK" else "OUT"


def fill_lineup(outs, target, avails=None, max_shift=None):
    """
    outs:   forecast() dicts for one group ('GK' or 'OUT') of one side in one
            fixture, availability already applied.
    avails: each player's availability (1.0 if not given).
    Returns them with P(start) shifted so the group adds up to target, or as
    close as MAX_SHIFT allows.
    """
    D = MAX_SHIFT if max_shift is None else max_shift
    avails = avails or [1.0] * len(outs)
    fixed, free = 0.0, []
    for i, (o, a) in enumerate(zip(outs, avails)):
        if a <= 0 or o["start"] <= 1e-9:
            continue
        c = min(o["start"] / a, 1.0)
        if c >= 1 - 1e-9:
            fixed += a
        else:
            free.append((i, a, _logit(c)))
    out = [dict(o) for o in outs]
    if not free:
        return out
    sig = lambda z: 1.0 / (1.0 + math.exp(-z))
    total = lambda d: fixed + sum(a * sig(l + d) for _, a, l in free)
    if total(D) <= target:
        d = D
    elif total(-D) >= target:
        d = -D
    else:
        lo, hi = -D, D
        for _ in range(60):
            m = (lo + hi) / 2
            if total(m) < target:
                lo = m
            else:
                hi = m
        d = (lo + hi) / 2
    for i, a, l in free:
        given = {k: v / a for k, v in outs[i].items()}     # as if available
        out[i] = {k: v * a for k, v in rescale(given, sig(l + d)).items()}
    return out


# ---- FPL's news ------------------------------------------------------------
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_BACK = re.compile(r"(?:expected back|suspended until)\s+(\d{1,2})\s+([a-z]{3})", re.I)


def return_date(news, today):
    """
    'Expected back 10 Oct' / 'Suspended until 19 Oct' -> a date, else None.
    The text has no year: take the first such date on or after 90 days ago.
    So 'Expected back 7 Jan' read in December is next January, 'Expected
    back 20 Apr' read in September is next April (a long-term injury), and a
    date a few weeks old stays in the past -- availability() decides what a
    stale date means.
    """
    m = _BACK.search(news or "")
    if not m:
        return None
    mon = _MONTHS.get(m.group(2).lower())
    if not mon:
        return None
    floor = today - datetime.timedelta(days=90)
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            d = datetime.date(y, mon, int(m.group(1)))
        except ValueError:
            return None
        if d >= floor:
            return d
    return None


def availability(status, cop, news, fixture_date, today, next_round):
    """
    Chance the player is available for a fixture, from FPL's flag.

    status: 'a' available, 'd' doubtful, 'i' injured, 's' suspended,
            'u' unavailable (left, loaned out), 'n' not in squad
    cop:    chance_of_playing_next_round (0-100), None when there is no news
    next_round: True for a fixture in the next gameweek

    The rules, plainly:
      - left the club or out on loan: out for everything
      - next gameweek: FPL's percentage whenever it gives one -- it is FPL's
        own call on exactly that round
      - after that, for a flagged player with a return date: out before it,
        fit from it. A date that has already passed while FPL still says
        injured or suspended is stale, and counts as no date
      - injured or suspended with no date: out until FPL gives one
      - otherwise fit
    """
    if status == "u":
        return 0.0
    if next_round and cop is not None:
        return max(0.0, min(1.0, cop / 100.0))
    if status in ("i", "s", "n", "d"):
        back = return_date(news, today)
        if back is not None and not (back < today and status in ("i", "s", "n")):
            return 0.0 if fixture_date < back else 1.0
    return 0.0 if status in ("i", "s", "n") else 1.0


# ---- reading FPL's data ----------------------------------------------------
def live_fixture_rows(lives, finished):
    """
    One row per player per finished team fixture, from FPL's
    event/{gw}/live/ replies: (player_id, fixture_id, (gw, kickoff), started, minutes).

    lives:    {gw: reply}. Each reply lists every player registered at the
              time, with one `explain` entry per team fixture that gameweek
              -- a 0-minute entry included when he did not play. That is
              exactly one row per team fixture while registered: the same
              rows the backtest was scored on.
    finished: {fixture_id: (gw, kickoff_iso)} for finished fixtures only, so
              a gameweek in progress contributes what has been played.

    FPL reports starts per gameweek, not per fixture. With one fixture that
    is exact. In a double gameweek the starts are credited to the fixtures
    he played longest in -- right unless he started one, came off early, and
    then played longer off the bench in the other.
    """
    out = []
    for gw in sorted(lives):
        for e in lives[gw].get("elements", []):
            fx = []
            for x in e.get("explain", []):
                if x["fixture"] not in finished:
                    continue
                mins = next((s["value"] for s in x["stats"] if s["identifier"] == "minutes"), 0)
                fx.append((x["fixture"], mins))
            starts = int((e.get("stats") or {}).get("starts") or 0)
            longest = sorted(range(len(fx)), key=lambda i: -fx[i][1])[:starts]
            for i, (f, m) in enumerate(fx):
                out.append((e["id"], f, finished[f], 1 if (i in longest and m > 0) else 0, m))
    return out


def live_rows(rows):
    """{player_id: [(started, minutes), ...] oldest first} from live_fixture_rows()."""
    per = {}
    for pid, f, key, st, m in rows:
        per.setdefault(pid, []).append((key, f, st, m))
    return {pid: [(st, m) for _, _, st, m in sorted(v)] for pid, v in per.items()}


def decode_prior(codes):
    """minutes_prior.json stores a start as 100 + minutes, anything else as minutes."""
    return [(1, c - 100) if c >= 100 else (0, c) for c in codes]
