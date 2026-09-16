"""
Bookmaker odds -> per-team expected goals.

Why this exists: the market is the sharpest forecast available for the next
week or two, and it is published before kick-off. That makes it both a source
for near-term projections and, more importantly, a weekly scorecard for the
xG model -- 20 teams graded every Friday instead of one noisy number a season.

Method follows claude/backtest-odds-benchmark.md:
  - average across books rather than trusting one (market_avg beat every
    single book in the 2025/26 join)
  - strip the vig proportionally
  - fit two independent Poisson means to P(home), P(draw), P(away), P(over 2.5)

The API key is read from the ODDS_API_KEY environment variable. It must never
be committed: put it in the repo's Actions secrets and expose it in update.yml
as `env: ODDS_API_KEY: ${{ secrets.ODDS_API_KEY }}`.
"""

import json
import math
import os
import urllib.parse
import urllib.request

API = "https://api.the-odds-api.com/v4/sports/soccer_epl/odds"
MAX_GOALS = 12

# The Odds API's names -> the short codes in data.json. Anything not listed
# falls through to a loud failure rather than a silent mismatch: an unnoticed
# name change is how a whole team quietly drops out of the table.
NAME_TO_SHORT = {
    "Arsenal": "ars", "Aston Villa": "avl", "Bournemouth": "bou",
    "Brentford": "bre", "Brighton and Hove Albion": "bha", "Burnley": "bur",
    "Chelsea": "che", "Coventry City": "cov", "Crystal Palace": "cry",
    "Everton": "eve", "Fulham": "ful", "Hull City": "hul",
    "Ipswich Town": "ips", "Leeds United": "lee", "Liverpool": "liv",
    "Manchester City": "mci", "Manchester United": "mun",
    "Newcastle United": "new", "Nottingham Forest": "nfo",
    "Sheffield United": "shu", "Sunderland": "sun",
    "Tottenham Hotspur": "tot", "West Ham United": "whu",
    "Wolverhampton Wanderers": "wol",
}


def fetch(api_key, regions="uk,eu", markets="h2h,totals"):
    """One call covers every upcoming fixture. Costs 1 credit per market per region."""
    qs = urllib.parse.urlencode({
        "apiKey": api_key, "regions": regions,
        "markets": markets, "oddsFormat": "decimal",
    })
    req = urllib.request.Request(f"{API}?{qs}", headers={"User-Agent": "fantasy-database"})
    with urllib.request.urlopen(req, timeout=30) as r:
        remaining = r.headers.get("x-requests-remaining")
        body = json.load(r)
    return body, remaining


def _avg_prices(event):
    """
    Average each outcome's implied probability across books.

    Averaging probabilities rather than decimal prices matters: the mean of
    1/p is not 1/mean(p), and the difference is not negligible on longshots.
    Only the 2.5 line is used, because it is the one every book quotes.
    """
    h2h, totals = {}, {}
    home, away = event["home_team"], event["away_team"]
    for bk in event.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt["key"] == "h2h":
                got = {o["name"]: o["price"] for o in mkt["outcomes"]}
                if {home, away, "Draw"} <= set(got):
                    for k, label in ((home, "H"), (away, "A"), ("Draw", "D")):
                        h2h.setdefault(label, []).append(1.0 / got[k])
            elif mkt["key"] == "totals":
                got = {o["name"]: o["price"] for o in mkt["outcomes"] if o.get("point") == 2.5}
                if {"Over", "Under"} <= set(got):
                    for k in ("Over", "Under"):
                        totals.setdefault(k, []).append(1.0 / got[k])
    if not {"H", "D", "A"} <= set(h2h) or not {"Over", "Under"} <= set(totals):
        return None
    mean = lambda xs: sum(xs) / len(xs)
    return (
        {k: mean(v) for k, v in h2h.items()},
        {k: mean(v) for k, v in totals.items()},
        len(event.get("bookmakers", [])),
    )


def _devig(d):
    """Proportional removal. backtest-odds-benchmark.md notes power de-vig moves
    probabilities by ~1pp; say which was used if a figure is ever published."""
    tot = sum(d.values())
    return {k: v / tot for k, v in d.items()}


def _poisson(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _outcome_probs(lh, la):
    """P(home win), P(draw), P(away win), P(over 2.5) under independent Poisson."""
    ph = [_poisson(i, lh) for i in range(MAX_GOALS)]
    pa = [_poisson(i, la) for i in range(MAX_GOALS)]
    H = D = A = over = 0.0
    for i, x in enumerate(ph):
        for j, y in enumerate(pa):
            p = x * y
            if i > j: H += p
            elif i == j: D += p
            else: A += p
            if i + j > 2: over += p
    return H, D, A, over


def solve(event):
    """
    Back out each side's expected goals from the de-vigged market.

    Independent Poisson cannot match 1x2 and over/under simultaneously -- the
    residual IS the low-score correlation the market prices and we don't (see
    the Dixon-Coles note in backtest-odds-benchmark.md). Least squares over the
    four targets spreads that error rather than honouring one market exactly.
    """
    got = _avg_prices(event)
    if got is None:
        return None
    h2h, totals, n_books = got
    h2h, totals = _devig(h2h), _devig(totals)
    target = (h2h["H"], h2h["D"], h2h["A"], totals["Over"])

    def loss(lh, la):
        return sum((a - b) ** 2 for a, b in zip(_outcome_probs(lh, la), target))

    # Coarse grid, then repeated local refinement. The surface is smooth and
    # two-dimensional, so this is both fast and immune to a bad starting point.
    best, step = (1.4, 1.2), 0.4
    best_loss = loss(*best)
    for _ in range(40):
        improved = False
        for dh in (-step, 0, step):
            for da in (-step, 0, step):
                lh, la = max(0.05, best[0] + dh), max(0.05, best[1] + da)
                l = loss(lh, la)
                if l < best_loss - 1e-15:
                    best, best_loss, improved = (lh, la), l, True
        if not improved:
            step /= 2
            if step < 1e-5:
                break
    return {
        "home": event["home_team"], "away": event["away_team"],
        "commence": event["commence_time"], "books": n_books,
        "home_xg": round(best[0], 4), "away_xg": round(best[1], 4),
        "fit_residual": round(best_loss, 8),
    }


def priced_fixtures(api_key=None):
    """
    Every fixture the books have priced, as
    {home: short, away: short, home_xg, away_xg, kickoff}.

    Returned per fixture rather than per team so the caller can join each one to
    its gameweek: bookmakers price whatever is next, which is usually one round
    but can be two, and a team with a double gameweek appears twice.
    """
    api_key = api_key or os.environ["ODDS_API_KEY"]
    events, remaining = fetch(api_key)
    out, unmapped = [], set()
    for ev in events:
        s = solve(ev)
        if not s:
            continue
        h, a = NAME_TO_SHORT.get(s["home"]), NAME_TO_SHORT.get(s["away"])
        if h is None or a is None:
            unmapped.update(t for t, k in ((s["home"], h), (s["away"], a)) if k is None)
            continue
        out.append({"home": h, "away": a, "home_xg": s["home_xg"],
                    "away_xg": s["away_xg"], "kickoff": s["commence"]})
    if unmapped:
        raise SystemExit(
            "Unmapped team names from the odds feed: " + ", ".join(sorted(unmapped))
            + "\nAdd them to NAME_TO_SHORT. Refusing to publish a partial table."
        )
    return out, remaining
