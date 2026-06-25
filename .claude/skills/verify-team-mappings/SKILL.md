---
name: verify-team-mappings
description: Verify every FBref team name maps to a canonical team BEFORE running a player backfill, covering both the schedule and player-match spellings. Use before backfilling a new competition or season, when an ingestion run skips fixtures or stops early, when you see UnknownTeamError or "not found in teams; add an entry to FBREF_TEAM_ALIASES", or when the user mentions team-name mismatches, aliases, or "map the teams".
---

# verify-team-mappings

Team-name reconciliation has silently broken backfills **repeatedly**. The cause is
always the same and is preventable.

## The two-spelling trap (read this first)

FBref spells the same club **two different ways**, and a backfill hits both:

- **Schedule page** → often short: `QPR`, `West Brom`, `Sheffield Weds`, `Preston`.
- **Player-match page scorebox** → full: `Queens Park Rangers`, `West Bromwich Albion`,
  `Sheffield Wednesday`, `Preston North End`.

`resolve_fbref_team` is **fail-loud**: one unmapped spelling makes the whole match
skip, so a single missing alias drops **every fixture involving that club** — and the
watchdog mis-reports the remainder as "unmatched on FBref". An alias diff that checks
only the schedule looks clean while half the season silently fails.

## Iron rule

**Never start a player backfill until the mapping check reports 0 gaps for BOTH
sources.** Checking one source (or eyeballing the schedule) is what keeps causing this.

## Workflow

1. **Run the check** (from `backend/`, venv):
   ```
   .venv/Scripts/python.exe -m ingestion.verify_team_aliases "<Competition>"
   ```
   It resolves every name from the schedule pages **and** the cached match-page
   scoreboxes (sampled per season), and prints any gaps as ready-to-paste alias lines.
2. **Add every gap** to `FBREF_TEAM_ALIASES` in `backend/ingestion/players.py`
   (canonical = the football-data.co.uk spelling already in `teams`). Use the tool's
   fuzzy suggestion, but confirm it against the `teams` table.
3. **Re-run** the check until it prints `OK — every team name resolves`.
4. **First-run caveat:** for a brand-new season with **no cached match pages yet**, the
   check can only see schedule spellings (it warns). Run a tiny backfill first
   (`players <season> "<Comp>" 2`) to cache a few match pages, OR run the full backfill
   once as a *discovery pass* — it is resumable and caches every page it fetches, so the
   re-run after adding aliases reads from cache (fast, no rate-limit) and ingests the
   skipped fixtures.

## Why a re-run is cheap

A skipped fixture still **fetched and cached** its match page (only the *ingest* failed
on the unmapped team). So after fixing aliases, re-triggering the backfill reads those
pages from soccerdata's local cache — minutes, not hours, and no fresh Cloudflare/rate
exposure.

## Related

- The canonical universe is football-data.co.uk (`teams.canonical_name`); never invent
  a team from FBref — a miss means a missing alias.
- After a stopped run, check the cause before re-running: `grep SKIP backfill.log` — a
  histogram of `UnknownTeamError` names IS the gap list.
