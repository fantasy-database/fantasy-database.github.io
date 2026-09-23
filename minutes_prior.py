"""
Builds minutes_prior.json: last season's per-fixture minutes for every
player, which the minutes model carries into the new season.

Run once each summer, after the season ends:

    python minutes_prior.py 2026-27

FPL's API forgets a season when the next one starts, so this reads the
archive at github.com/vaastav/Fantasy-Premier-League instead. Players are
keyed by FPL's `code`, which -- unlike the player id -- stays the same from
one season to the next.

Each player is a list of his team's fixtures while he was registered,
oldest first: 100 + minutes if he started, otherwise just the minutes.
"""
import csv, io, json, sys, pathlib, urllib.request

BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
OUT = pathlib.Path(__file__).parent / "minutes_prior.json"
POS = {"GK", "DEF", "MID", "FWD"}          # drops assistant managers


def fetch_csv(season, name):
    with urllib.request.urlopen(f"{BASE}/{season}/{name}", timeout=60) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))


def build(season):
    code = {r["id"]: r["code"] for r in fetch_csv(season, "players_raw.csv")}
    rows, seen = {}, set()
    for r in fetch_csv(season, "gws/merged_gw.csv"):
        if r["position"] not in POS:
            continue
        key = (r["element"], r["fixture"])
        if key in seen:                       # the archive repeats a few rows
            continue
        seen.add(key)
        m, st = int(r["minutes"]), int(r["starts"])
        assert 0 <= m <= 99, f"minutes {m} would break the encoding"
        rows.setdefault(code[r["element"]], []).append(
            ((int(r["GW"]), r["kickoff_time"], int(r["fixture"])), 100 + m if st else m))

    players = {c: [v for _, v in sorted(rs)] for c, rs in rows.items()}
    starts = sum(v >= 100 for rs in players.values() for v in rs)
    fixtures = len({k[1] for k in seen})
    # every fixture has exactly 22 starters; anything else means bad data
    if starts != fixtures * 22:
        raise SystemExit(f"{starts} starts over {fixtures} fixtures -- expected {fixtures * 22}")
    return {"season": season.replace("-", "/"), "source": "vaastav/Fantasy-Premier-League",
            "encoding": "per player code, oldest first: 100 + minutes if started, else minutes",
            "players": players}


if __name__ == "__main__":
    season = sys.argv[1] if len(sys.argv) > 1 else "2025-26"
    out = build(season)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    n = sum(len(v) for v in out["players"].values())
    print(f"wrote {OUT.name}: {out['season']}, {len(out['players'])} players, {n} player-fixtures")
