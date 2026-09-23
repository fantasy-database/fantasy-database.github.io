"""
Builds minutes_prior.json and points_prior.json: last season's per-fixture
minutes for every player, which the minutes model carries into the new
season, and each player's end-of-season record for the points model
(points.py: shares of his side's xG and xA, saves, DefCon, cards).

Run once each summer, after the season ends:

    python minutes_prior.py 2026-27

FPL's API forgets a season when the next one starts, so this reads the
archive at github.com/vaastav/Fantasy-Premier-League instead. Players are
keyed by FPL's `code`, which -- unlike the player id -- stays the same from
one season to the next.

minutes_prior.json: each player is a list of his team's fixtures while he
was registered, oldest first: 100 + minutes if he started, otherwise just the
minutes.

points_prior.json: each player's decayed record at the end of the season,
folded with points.LAM_P. It depends on that constant, so rebuild it
whenever LAM_P changes.
"""
import csv, io, json, sys, pathlib, urllib.request

import points as PT

BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
OUT = pathlib.Path(__file__).parent / "minutes_prior.json"
OUT_PTS = pathlib.Path(__file__).parent / "points_prior.json"
POS = {"GK", "DEF", "MID", "FWD"}          # drops assistant managers


def fetch_csv(season, name):
    with urllib.request.urlopen(f"{BASE}/{season}/{name}", timeout=60) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))


def build(season):
    code = {r["id"]: r["code"] for r in fetch_csv(season, "players_raw.csv")}
    sides = {int(r["id"]): (int(r["team_h"]), int(r["team_a"])) for r in fetch_csv(season, "fixtures.csv")}
    merged = fetch_csv(season, "gws/merged_gw.csv")
    rows, seen, prow = {}, set(), {}
    side_xg, once = {}, set()
    for r in merged:
        if r["position"] not in POS or (r["element"], r["fixture"]) in once:
            continue
        once.add((r["element"], r["fixture"]))
        home = r["was_home"] in ("True", "true", "1")
        t = sides[int(r["fixture"])][0 if home else 1]
        side_xg[(int(r["fixture"]), t)] = side_xg.get((int(r["fixture"]), t), 0.0) + float(r["expected_goals"] or 0)
    for r in merged:
        if r["position"] not in POS:
            continue
        key = (r["element"], r["fixture"])
        if key in seen:                       # the archive repeats a few rows
            continue
        seen.add(key)
        m, st = int(r["minutes"]), int(r["starts"])
        assert 0 <= m <= 99, f"minutes {m} would break the encoding"
        order = (int(r["GW"]), r["kickoff_time"], int(r["fixture"]))
        rows.setdefault(code[r["element"]], []).append((order, 100 + m if st else m))
        f = int(r["fixture"]); home = r["was_home"] in ("True", "true", "1")
        t, o = (sides[f] if home else sides[f][::-1])
        prow.setdefault(code[r["element"]], []).append((order, dict(
            m=m, xg=float(r["expected_goals"] or 0), xa=float(r["expected_assists"] or 0),
            tf=side_xg.get((f, t), 0.0), ta=side_xg.get((f, o), 0.0), sv=int(r["saves"]),
            dc=int(r["defensive_contribution"]) if r.get("defensive_contribution") not in (None, "") else None,
            yc=int(r["yellow_cards"]), rc=int(r["red_cards"]),
            misc=5 * int(r["penalties_saved"]) - 2 * int(r["penalties_missed"]) - 2 * int(r["own_goals"]),
            pos=r["position"])))

    players = {c: [v for _, v in sorted(rs)] for c, rs in rows.items()}
    starts = sum(v >= 100 for rs in players.values() for v in rs)
    fixtures = len({k[1] for k in seen})
    # every fixture has exactly 22 starters; anything else means bad data
    if starts != fixtures * 22:
        raise SystemExit(f"{starts} starts over {fixtures} fixtures -- expected {fixtures * 22}")
    states = {}
    for c, rs in prow.items():
        st = PT.empty()
        for _, r in sorted(rs, key=lambda x: x[0]):
            st = PT.step(st, r, PT.LAM_P)
        if st["M90"] > 0:
            states[c] = {k: round(v, 4) for k, v in st.items() if v}
    pts = {"season": season.replace("-", "/"), "source": "vaastav/Fantasy-Premier-League",
           "lam": PT.LAM_P, "about": "each player's points.py record at the end of the season, "
           "before the summer discount (points.DELTA_P)", "players": states}
    return {"season": season.replace("-", "/"), "source": "vaastav/Fantasy-Premier-League",
            "encoding": "per player code, oldest first: 100 + minutes if started, else minutes",
            "players": players}, pts


if __name__ == "__main__":
    season = sys.argv[1] if len(sys.argv) > 1 else "2025-26"
    out, pts = build(season)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    OUT_PTS.write_text(json.dumps(pts, separators=(",", ":")))
    n = sum(len(v) for v in out["players"].values())
    print(f"wrote {OUT.name}: {out['season']}, {len(out['players'])} players, {n} player-fixtures")
    print(f"wrote {OUT_PTS.name}: {len(pts['players'])} players with minutes")
