"""
Score the fixture model against the bookmakers, every week.

Run it after a build:  python market_check.py
                       python market_check.py --cached sample_market.json

It prints, per team, what the model projects for the next gameweek and what the
market implies, plus bias / RMSE / correlation across all 20. Those three
numbers are the weekly scorecard: RMSE-to-market is the cheapest validation
this project has (claude/backtest-odds-benchmark.md), because the odds are
published before kick-off and grade 20 teams at a time.

It changes nothing on the site. It tells you where the ratings are wrong.
"""

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_data(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def strengths(teams, n, k_atk, k_def, k_promoted):
    """Mirrors strengths() in model.js: blend, then normalise to a league mean of 1."""
    b = {}
    for tid, t in teams.items():
        promoted = bool(t.get("promoted"))
        wa = n / (n + (k_promoted if promoted else k_atk))
        wd = n / (n + (k_promoted if promoted else k_def))
        b[tid] = [wa * t["a26"] + (1 - wa) * t["pa"],
                  wd * t["d26"] + (1 - wd) * t["pd"]]
    ma = sum(v[0] for v in b.values()) / len(b)
    md = sum(v[1] for v in b.values()) / len(b)
    base = (ma + md) / 2
    return ({k: v[0] / ma for k, v in b.items()},
            {k: v[1] / md for k, v in b.items()}, base)


def model_projections(d, gw):
    atk, dfn, base = strengths(d["teams"], d["matchesPlayed"],
                               d["fit"]["kAtk"], d["fit"]["kDef"], 4)
    h, pen = d["fit"]["home"], d["fit"]["pen"]
    out = {}
    for fx in d["fixtures"]:
        gwk, home, away = fx[0], str(fx[1]), str(fx[2])
        if gwk != gw:
            continue
        out[d["teams"][home]["short"]] = base * atk[home] * dfn[away] * h * pen
        out[d["teams"][away]["short"]] = base * atk[away] * dfn[home] / h * pen
    return out


def stats(pairs):
    n = len(pairs)
    e = [a - b for a, b in pairs]
    bias = sum(e) / n
    rmse = math.sqrt(sum(x * x for x in e) / n)
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sxx = sum((a - mx) ** 2 for a, _ in pairs)
    syy = sum((b - my) ** 2 for _, b in pairs)
    r = sxy / math.sqrt(sxx * syy) if sxx and syy else float("nan")
    return n, bias, rmse, r, (sxy / sxx if sxx else float("nan"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "data.json"))
    ap.add_argument("--cached", help="a saved market table, to score without spending credits")
    ap.add_argument("--gw", type=int, help="gameweek (default: data.json's nextGw)")
    args = ap.parse_args()

    d = load_data(args.data)
    gw = args.gw or d["nextGw"]

    if args.cached:
        with open(args.cached, encoding="utf-8") as f:
            market = json.load(f)
        remaining = "n/a (cached)"
    else:
        import odds
        raw, remaining = odds.market_table()
        market = {k: v[0]["xg"] for k, v in raw.items() if v}

    mine = model_projections(d, gw)
    common = sorted(set(mine) & set(market), key=lambda s: -(mine[s] - market[s]))
    missing = sorted(set(mine) - set(market))

    print(f"GW{gw} · model vs market · {len(common)} teams · credits left: {remaining}\n")
    print("team    model   market    diff")
    for s in common:
        print(f"{s.upper():5}  {mine[s]:6.2f}  {market[s]:6.2f}  {mine[s]-market[s]:+6.2f}")
    if missing:
        print("\nno market price yet:", ", ".join(x.upper() for x in missing))

    n, bias, rmse, r, slope = stats([(mine[s], market[s]) for s in common])
    print(f"\n  n {n}   bias {bias:+.3f}   RMSE {rmse:.3f}   r {r:.3f}   slope {slope:.3f}")
    print("\n  bias  : model projects more (+) or fewer (-) goals than the market, per team")
    print("  RMSE  : typical distance from the market. Lower is better. Track it weekly.")
    print("  slope : ~1 means the spread is right. >1 too compressed, <1 too extreme.")


if __name__ == "__main__":
    main()
