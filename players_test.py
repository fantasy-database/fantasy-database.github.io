"""
Checks on update.build_players: the actual-output fields that sit beside
npxG and xA on the players page (NP Goals and Assists, totals and per 90).

No network: Understat's reply is stubbed. Run: python players_test.py
"""
import pathlib, sys

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


def element(i, first, second, web, pos, mins, goals, assists):
    return {"id": i, "first_name": first, "second_name": second, "web_name": web,
            "team": 1, "element_type": pos, "now_cost": 80, "selected_by_percent": "12.5",
            "status": "a", "news": "", "chance_of_playing_next_round": None,
            "minutes": mins, "starts": mins // 90, "total_points": 50,
            "defensive_contribution": 20, "saves": 0, "clean_sheets": 2,
            "goals_conceded": 5, "expected_goals_conceded": "4.10",
            "goals_scored": goals, "assists": assists,
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

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
