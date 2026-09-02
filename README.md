# FANTASY DATABASE

An expected-goals fixture ticker and player database for Fantasy Premier League.

**Live site:** https://nahomfitsum6gh.github.io/fpl-ticker/

## What it does

- **Fixture ticker** — replaces FPL's 1–5 difficulty rating with an expected-goals
  model. Attack and defence are rated separately, since scoring goals and keeping
  clean sheets do not track together. Home advantage scales with the opponent.
- **Player database** — points, minutes, defensive contributions, saves, clean
  sheets, non-penalty xG, xA and shots, with per-90 counterparts and a minutes
  floor so short cameos cannot fake a good rate.

## How it stays current

`update.py` rebuilds `data.json` and `players.json` from the FPL API and Understat.
A GitHub Action runs it daily at 06:30 UTC and again on Friday afternoons for
press-conference team news. Nothing needs to be done by hand.

## Files

| File | Purpose |
| --- | --- |
| `index.html` | Fixture ticker |
| `players.html` | Player database |
| `update.py` | Builds both data files |
| `data.json` / `players.json` | Generated — do not edit by hand |
