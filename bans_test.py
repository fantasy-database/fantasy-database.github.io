"""
Checks on the yellow-card ban tracker in update.py: ban_status (the active
threshold and bookings to a ban), team_games_played and add_bans.

No network. Run: python bans_test.py
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


B = U.ban_status

print("ban_status")
r = B(4, 12, "a")
check("4 yellows, 12 games -> 4/5, one booking away",
      (r["yc"], r["yth"], r["ytb"], r["sus"]) == (4, 5, 1, False), r)
r = B(6, 12, "a")
check("6 yellows -> 6/10", (r["yth"], r["ytb"]) == (10, 4), r)
check("0 yellows, GW1 -> 0/5", B(0, 0, "a")["yth"] == 5)
check("4 yellows after the 19th-game cut-off -> 4/10", B(4, 19, "a")["yth"] == 10)
check("4 yellows at 18 games -> still 4/5", B(4, 18, "a")["yth"] == 5)
check("exactly 5 (ban served) -> next is 10", B(5, 10, "a")["yth"] == 10)
check("9 yellows at 31 games -> 9/10", B(9, 31, "a")["ytb"] == 1)
check("9 yellows at 32 games -> 9/15", B(9, 32, "a")["yth"] == 15)
check("2 yellows at 32 games -> 2/15", B(2, 32, "a")["yth"] == 15)
check("14 yellows at 37 games -> 14/15", B(14, 37, "a")["ytb"] == 1)
r = B(15, 30, "a")
check("15 or more -> no threshold left", (r["yth"], r["ytb"]) == (None, None), r)
r = B(5, 10, "s")
check("status 's' -> flagged suspended, no threshold",
      (r["sus"], r["yth"], r["ytb"], r["yc"]) == (True, None, None, 5), r)
check("doubtful is not suspended", B(4, 10, "d")["sus"] is False)

print("team_games_played")
fx = [
    {"team_h": 1, "team_a": 2, "finished": True},
    {"team_h": 3, "team_a": 1, "finished": False, "finished_provisional": True},
    {"team_h": 2, "team_a": 3, "finished": False, "finished_provisional": False},
    {"team_h": 1, "team_a": 3},                         # not played yet
]
g = U.team_games_played(fx)
check("finished and provisionally finished both count",
      (g[1], g[2], g[3]) == (2, 1, 1), dict(g))

print("add_bans")
# team 1 has played 18 games, team 2 has played 25
raw = ([{"team_h": 1, "team_a": 3, "finished": True}] * 18
       + [{"team_h": 2, "team_a": 4, "finished": True}] * 25)
elements = [
    {"id": 1, "team": 1, "yellow_cards": 4, "status": "a"},
    {"id": 2, "team": 1, "yellow_cards": 6, "status": "a"},
    {"id": 3, "team": 2, "yellow_cards": 4, "status": "a"},
    {"id": 4, "team": 2, "yellow_cards": 5, "status": "s"},
    {"id": 5, "team": 5, "yellow_cards": 0, "status": "a"},   # no games yet
]
players = [{"i": e["id"]} for e in elements]
one = U.add_bans(players, elements, raw)
by = {p["i"]: p for p in players}
check("4 yellows, club on 18 games -> 4/5", (by[1]["yc"], by[1]["yth"], by[1]["ytb"]) == (4, 5, 1), by[1])
check("6 yellows -> 6/10", (by[2]["yc"], by[2]["yth"]) == (6, 10), by[2])
check("4 yellows, club on 25 games -> 4/10", by[3]["yth"] == 10, by[3])
check("suspended flagged", by[4]["sus"] is True and by[4]["ytb"] is None, by[4])
check("club with no games -> 0/5", by[5]["yth"] == 5, by[5])
check("returns the count one booking away", one == 1, one)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
