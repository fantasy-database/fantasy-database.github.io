"""
Checks on update.build_players: the actual-output fields that sit beside
npxG and xA on the players page (NP Goals and Assists, totals and per 90).

No network: Understat's reply is stubbed. Run: python players_test.py
"""
import json, pathlib, sys, tempfile

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


U.log = lambda *a, **k: None


def element(i, first, second, web, pos, mins, goals, assists, xg="0.0", xa="0.0"):
    return {"id": i, "first_name": first, "second_name": second, "web_name": web,
            "team": 1, "element_type": pos, "now_cost": 80, "selected_by_percent": "12.5",
            "status": "a", "news": "", "chance_of_playing_next_round": None,
            "minutes": mins, "starts": mins // 90, "total_points": 50,
            "defensive_contribution": 20, "saves": 0, "clean_sheets": 2,
            "goals_conceded": 5, "expected_goals_conceded": "4.10",
            "goals_scored": goals, "assists": assists,
            "expected_goals": xg, "expected_assists": xa,
            "penalties_order": None, "corners_and_indirect_freekicks_order": None,
            "direct_freekicks_order": None}


def und(name, npxg, xa, npg, shots, mins, **extra):
    row = {"player_name": name, "npxG": str(npxg), "xA": str(xa), "npg": str(npg),
           "goals": str(npg), "shots": str(shots), "time": str(mins)}
    row.update(extra)
    return row


boot = {"elements": [
    # a penalty taker: 6 FPL goals, 2 of them penalties
    element(1, "Penny", "Taker", "Taker", 4, 900, 6, 3),
    # a midfielder with no Understat row at all
    element(2, "Nobody", "Known", "Known", 3, 450, 1, 2),
    # a defender who has not played
    element(3, "Bench", "Warmer", "Warmer", 2, 0, 0, 0),
]}
UND_PLAYERS = [und("Penny Taker", 3.4, 1.2, 4, 30, 900),
               und("Bench Warmer", 0.0, 0.0, 0, 0, 0)]

real_get_json = U.get_json
U.get_json = lambda url, headers=None, tries=4: {"players": UND_PLAYERS}
try:
    out = {p["i"]: p for p in U.build_players(boot, 2026, {"aaa": "Arsenal"}, {"aaa": 1}, 10)}
finally:
    U.get_json = real_get_json

t, n, b = out[1], out[2], out[3]
check("npg is Understat's non-penalty goals, not FPL's", t["npg"] == 4 and t["g"] == 6, t)
check("npg90 per 90 from the same minutes as npxg90",
      abs(t["npg90"] - 0.4) < 1e-9 and abs(t["npxg90"] - 0.34) < 1e-9, t)
check("a stays FPL assists", t["a"] == 3)
check("a90 per 90", abs(t["a90"] - 0.3) < 1e-9, t)
check("no Understat row: npg and npg90 are null, not zero",
      n["npg"] is None and n["npg90"] is None, n)
check("no Understat row: FPL assists still give a90", abs(n["a90"] - 0.4) < 1e-9, n)
check("no minutes: per-90 fields are null", b["npg90"] is None and b["a90"] is None, b)
check("no minutes: totals are still zero", b["npg"] == 0 and b["a"] == 0, b)

# Understat dropping npg must not cost the team its npxG
UND_PLAYERS = [{k: v for k, v in r.items() if k != "npg"} for r in UND_PLAYERS]
U.get_json = lambda url, headers=None, tries=4: {"players": UND_PLAYERS}
try:
    out = {p["i"]: p for p in U.build_players(boot, 2026, {"aaa": "Arsenal"}, {"aaa": 1}, 10)}
finally:
    U.get_json = real_get_json
check("missing npg field: npxG still joined", out[1]["npxg"] == 3.4, out[1])
check("missing npg field: npg is null", out[1]["npg"] is None and out[1]["npg90"] is None, out[1])

# a malformed npg value must not cost the team its npxG either
UND_PLAYERS = [dict(r, npg="") for r in UND_PLAYERS]
U.get_json = lambda url, headers=None, tries=4: {"players": UND_PLAYERS}
try:
    out = {p["i"]: p for p in U.build_players(boot, 2026, {"aaa": "Arsenal"}, {"aaa": 1}, 10)}
finally:
    U.get_json = real_get_json
check("malformed npg value: npxG still joined, npg null",
      out[1]["npxg"] == 3.4 and out[1]["npg"] is None, out[1])

# --- name matching: one Understat row, several FPL "Gabriel"s -------------
def build_with(elements, rows):
    U.get_json = lambda url, headers=None, tries=4: {"players": rows}
    try:
        return {p["i"]: p for p in U.build_players({"elements": elements}, 2026,
                                                   {"aaa": "Arsenal"}, {"aaa": 1}, 10)}
    finally:
        U.get_json = real_get_json

gab = build_with([element(4, "Gabriel", "dos Santos Magalhães", "Gabriel", 2, 900, 1, 0),
                  element(18, "Gabriel", "Martinelli Silva", "Martinelli", 3, 0, 0, 0),
                  element(27, "Gabriel Fernando", "de Jesus", "G.Jesus", 4, 0, 0, 0)],
                 [und("Gabriel", 2.1, 0.06, 1, 9, 900)])
check("Gabriel row: Magalhães (900 min) matches", gab[4]["npxg"] == 2.1 and gab[4]["sh"] == 9, gab[4])
check("Gabriel row: Martinelli (0 min) does not",
      gab[18]["npxg"] is None and gab[18]["sh"] is None, gab[18])
check("Gabriel row: G.Jesus (0 min) does not",
      gab[27]["npxg"] is None and gab[27]["sh"] is None, gab[27])

# two loose matches for one row, both within 180 minutes: the closer keeps it
two = build_with([element(5, "Alex", "Smith Jones", "Smith", 3, 700, 0, 0),
                  element(6, "Alex", "Smith Brown", "A.Smith", 3, 860, 0, 0)],
                 [und("Alex Smith", 1.5, 0.5, 1, 12, 820)])
check("shared loose match: closer minutes keep the row",
      two[6]["npxg"] == 1.5 and two[5]["npxg"] is None, (two[5], two[6]))

# --- Understat down: FPL is the fallback ----------------------------------
called = []
U.get_json = lambda *a, **k: called.append(a) or {"players": []}
try:
    off = {p["i"]: p for p in U.build_players(
        {"elements": [element(249, "Charlie", "Barry", "Barry", 4, 851, 6, 1, xg="6.1", xa="0.32"),
                      element(18, "Gabriel", "Martinelli Silva", "Martinelli", 3, 0, 0, 0)]},
        2026, {"aaa": "Arsenal"}, {"aaa": 1}, 10, offline=True)}
finally:
    U.get_json = real_get_json
bar = off[249]
check("offline: Understat not called", called == [], called)
check("offline: npg is FPL goals", bar["npg"] == 6 and bar["g"] == 6, bar)
check("offline: npxg / xa are FPL's", bar["npxg"] == 6.1 and bar["xa"] == 0.32, bar)
check("offline: npxgi = xg + xa", bar["npxgi"] == 6.42, bar)
check("offline: per-90s recomputed",
      bar["npg90"] == round(6 / 851 * 90, 3) and bar["npxg90"] == round(6.1 / 851 * 90, 3)
      and bar["xa90"] == round(0.32 / 851 * 90, 3) and bar["npxgi90"] == round(6.42 / 851 * 90, 3), bar)
check("offline: no shots until carried", bar["sh"] is None and bar["sh90"] is None, bar)
check("offline: 0 minutes gives FPL's 0.0 and null per-90",
      off[18]["npxg"] == 0.0 and off[18]["npxg90"] is None, off[18])

tmp = pathlib.Path(tempfile.mkdtemp()) / "players.json"
tmp.write_text(json.dumps({"generated": "2026-11-09T11:00:00+00:00", "players": [
    {"i": 249, "t": 9, "min": 800, "npxg": 6.44, "xa": 0.32, "sh": 27, "sh90": 3.563, "npg": 3},
    {"i": 4, "t": 1, "min": 900, "npxg": 2.1, "xa": 0.06, "sh": 9, "sh90": 1.0, "npg": 1},
    {"i": 18, "t": 1, "min": 0, "npxg": 2.1, "xa": 0.06, "sh": 9, "sh90": None, "npg": 1},
    {"i": 27, "t": 1, "min": 0, "npxg": 2.1, "xa": 0.06, "sh": 9, "sh90": None, "npg": 1},
]}))
src = U.xg_source(False, "2026-11-09T12:00:00+00:00", tmp, "2026-11-01T21:12:47+00:00")
check("xg_source down: fpl, ratingsAsOf as understatAsOf, shots stale",
      src == {"xgSource": "fpl", "understatAsOf": "2026-11-01T21:12:47+00:00",
              "stale": ["sh", "sh90"]}, src)
check("xg_source up: understat, as of now",
      U.xg_source(True, "2026-11-09T12:00:00+00:00", tmp)
      == {"xgSource": "understat", "understatAsOf": "2026-11-09T12:00:00+00:00"})
tmp2 = tmp.with_name("p2.json")
tmp2.write_text(json.dumps({"understatAsOf": "2026-11-01T20:00:00+00:00", "players": []}))
check("xg_source down: keeps the old understatAsOf",
      U.xg_source(False, "x", tmp2, "y")["understatAsOf"] == "2026-11-01T20:00:00+00:00")

ps = [dict(bar), {"i": 4, "t": 1, "min": 900, "npxg": 1.8, "npg": 1, "sh": None, "sh90": None},
      {"i": 18, "t": 1, "min": 0, "npxg": 0.0, "npg": 0, "sh": None, "sh90": None},
      {"i": 27, "t": 1, "min": 0, "npxg": 0.0, "npg": 0, "sh": None, "sh90": None},
      {"i": 999, "t": 1, "min": 90, "npxg": 0.1, "npg": 0, "sh": None, "sh90": None}]
n = U.carry_shots(ps, tmp)
by = {p["i"]: p for p in ps}
check("carry_shots: shots carried", by[249]["sh"] == 27 and by[249]["sh90"] == 3.563, by[249])
check("carry_shots: FPL goals and xG untouched", by[249]["npg"] == 6 and by[249]["npxg"] == 6.1, by[249])
check("carry_shots: shared Gabriel row only to the most minutes",
      by[4]["sh"] == 9 and by[18]["sh"] is None and by[27]["sh"] is None, (by[4], by[18], by[27]))
check("carry_shots: a new player keeps null shots", by[999]["sh"] is None)
check("carry_shots: count", n == 2, n)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
