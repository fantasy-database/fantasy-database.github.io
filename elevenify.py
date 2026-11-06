"""
Elevenify's season-long projections, as a second outside view of each side.

elevenify.com (a free Substack by a top FPL manager) publishes a live Google
Sheet for subscribers: projected goals and clean-sheet chance for every team
in every gameweek, re-simulated from its team ratings after each round. The
author asks that people who use it share it; the site credits it in every
page footer.

The sheet has no opponents, only team x gameweek, and places each fixture in
the gameweek it had when the season's fixture list came out. So nothing here
trusts that a cell is the fixture FPL now has in that gameweek. Its clean
sheet column is exactly exp(-opponent's projected goals), which pins the
pairing down: a fixture is used only if each side's clean-sheet chance matches
the other side's goals. A rearranged game, a double or a blank fails that and
is dropped.

The kept fixtures go through odds.implied_ratings() -- the same solver that
turns bookmakers' prices into a market attack and defence -- to give eA / eD
(from eN fixtures) per side, which model.js blends into the ratings the way it
blends the market's (weight eN/(eN + kEleven)).

No network here: update.py fetches the sheet and hands its text to parse().
"""

import csv, io, math

SHEET = ("https://docs.google.com/spreadsheets/d/"
         "1rJaMd03-t5ovug0Zn8fY9jJ1_H0C8PDSC10HqbdwSyk/export?format=csv&gid=0")

# Elevenify's name -> FPL's, where they differ. Every other side is spelt the same.
NAMES = {"Coventry": "Coventry City", "Hull": "Hull City", "Ipswich": "Ipswich Town"}

WINDOW = 6        # upcoming rounds read, from the next one on
MIN_ROUNDS = 3    # fewer usable rounds than this and nothing is returned
PAIR_TOL = 0.02   # |clean sheet - exp(-opponent goals)| allowed for a fixture to count

SECTIONS = {"projected goals": "goals", "projected clean sheets": "cs"}


def parse(text):
    """
    The sheet's CSV export -> {"goals": {name: {gw: x}}, "cs": {name: {gw: p}}}.

    Finds each section by its title, then the header row holding "Team" and
    GW1..GW38 below it, and reads team rows until the first blank one. Raises
    ValueError if either section is missing or does not hold 20 sides.
    """
    rows = list(csv.reader(io.StringIO(text)))
    out = {}
    i = 0
    while i < len(rows):
        cells = [c.strip() for c in rows[i]]
        key = next((SECTIONS[c.lower()] for c in cells if c.lower() in SECTIONS), None)
        if key is None:
            i += 1
            continue
        # the header row: first row after the title with a "Team" cell
        j = i + 1
        while j < len(rows) and "Team" not in [c.strip() for c in rows[j]]:
            j += 1
        if j >= len(rows):
            raise ValueError(f"no header row under '{key}'")
        head = [c.strip() for c in rows[j]]
        tcol = head.index("Team")
        gcols = {int(c[2:]): k for k, c in enumerate(head) if c.upper().startswith("GW") and c[2:].isdigit()}
        table = {}
        j += 1
        while j < len(rows):
            r = rows[j]
            name = r[tcol].strip() if tcol < len(r) else ""
            if not name:
                break
            vals = {}
            for gw, k in gcols.items():
                v = r[k].strip() if k < len(r) else ""
                if v:
                    try:
                        vals[gw] = float(v.replace("%", "")) / (100 if v.endswith("%") else 1)
                    except ValueError:
                        pass             # "-", "#N/A" and the like: that cell is skipped

            table[name] = vals
            j += 1
        out[key] = table
        i = j
    for key in ("goals", "cs"):
        if key not in out:
            raise ValueError(f"the sheet has no '{key}' section")
        if len(out[key]) != 20:
            raise ValueError(f"'{key}' has {len(out[key])} sides, expected 20")
    return out


def name_map(sheet_names, teams):
    """Elevenify name -> FPL team id (as a string). Raises if any side is unmatched."""
    by_name = {t["name"].lower(): str(tid) for tid, t in teams.items()}
    out, missing = {}, []
    for n in sheet_names:
        tid = by_name.get(NAMES.get(n, n).lower())
        if tid is None:
            missing.append(n)
        else:
            out[n] = tid
    if missing:
        raise ValueError(f"no FPL side for Elevenify's {', '.join(missing)}")
    if len(set(out.values())) != len(out):
        raise ValueError("two Elevenify sides map to the same FPL side")
    return out


def fixtures_by_gw(sheet, teams, fixtures, first_gw, last_gw, tol=PAIR_TOL):
    """
    The fixtures of rounds first_gw..last_gw that Elevenify's sheet projects,
    as odds.implied_ratings() wants them: {str(gw): [[home short, away short,
    home goals, away goals], ...]}. Returns (history, dropped), dropped
    listing the fixtures left out and why.

    fixtures are data.json's: [gw, home id, away id, ...].
    """
    tid_of = name_map(sheet["goals"], teams)
    name_of = {tid: n for n, tid in tid_of.items()}
    short = {str(tid): t["short"] for tid, t in teams.items()}
    games = {}
    for f in fixtures:
        gw = f[0]
        if gw is None or not (first_gw <= gw <= last_gw):
            continue
        for side in (str(f[1]), str(f[2])):
            games[(gw, side)] = games.get((gw, side), 0) + 1
    hist, dropped = {}, []
    for f in fixtures:
        gw, h, a = f[0], str(f[1]), str(f[2])
        if gw is None or not (first_gw <= gw <= last_gw):
            continue
        label = f"GW{gw} {short.get(h, h)}-{short.get(a, a)}"
        if games[(gw, h)] != 1 or games[(gw, a)] != 1:
            dropped.append(f"{label} (double gameweek)")
            continue
        hn, an = name_of.get(h), name_of.get(a)
        try:
            gh, ga = sheet["goals"][hn][gw], sheet["goals"][an][gw]
            ch, ca = sheet["cs"][hn][gw], sheet["cs"][an][gw]
        except KeyError:
            dropped.append(f"{label} (not in the sheet)")
            continue
        if abs(ch - math.exp(-ga)) > tol or abs(ca - math.exp(-gh)) > tol:
            dropped.append(f"{label} (sheet has a different fixture in that round)")
            continue
        hist.setdefault(str(gw), []).append([short[h], short[a], round(gh, 3), round(ga, 3)])
    return hist, dropped
