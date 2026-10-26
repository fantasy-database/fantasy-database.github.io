# FANTASY DATABASE

An expected-goals fixture ticker and player database for Fantasy Premier League.

**Live site:** https://fantasydatabase.app

## What it does

- **Fixture ticker** — replaces FPL's 1–5 difficulty rating with an expected-goals
  model. Attack and defence are rated separately, since scoring goals and keeping
  clean sheets do not track together. Home advantage scales with the opponent.
- **Player database** — points, minutes, defensive contributions, saves, clean
  sheets, non-penalty xG, xA and shots, with per-90 counterparts and a minutes
  floor so short cameos cannot fake a good rate.

## How it stays current

`update.py` rebuilds `data.json` and `players.json` from the FPL API and Understat.
A GitHub Action wakes every hour; `cadence.py` decides what each wake-up does.
The site rebuilds every two hours (hourly in the six before a deadline).
Bookmaker odds, which cost credits, are fetched about every seven hours, plus
the day before and twice in the last three hours before each deadline. That is
about 450 of The Odds API's 500 free credits a month; the balance is checked
free of charge every hour, and `cadence.json` records each paid request so a
failed build can never trigger a second one. Nothing needs to be done
by hand; the Actions tab's "Run workflow" button forces a rebuild, with or
without odds.

## Files

| File | Purpose |
| --- | --- |
| `index.html` | Fixture ticker |
| `players.html` | Player database |
| `privacy.html` | Privacy notice (linked from every page's footer) |
| `fonts/` | Self-hosted Barlow and Roboto Mono, so no page calls Google |
| `update.py` | Builds both data files |
| `data.json` / `players.json` | Generated — do not edit by hand |
