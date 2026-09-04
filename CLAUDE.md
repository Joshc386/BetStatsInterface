# CLAUDE.md — Football Betting-Research Platform

Standing constraints for this project. Loaded every turn — keep edits tight.
Full spec lives in `betting-research-platform-scope.md`; this file is the **rules**, not the feature list.

---

## Project

Personal, single-user web app for **betting research**. Ingests free football data into local Postgres; serves team/player rolling-window stats, market filters, and fixture views. It is a **research tool, not a bot and not a predictive model**.

**Stack:** FastAPI · PostgreSQL · React (Vite + TypeScript + Tailwind) · `soccerdata` for ingestion.

---

## NON-NEGOTIABLES (do not violate these)

1. **Sources are ingestion-only.** FBref / football-data.co.uk / ESPN are touched **exclusively** by scheduled ingestion jobs. The API and frontend read **only** from Postgres. Never put a scrape/fetch in a request path.
2. **Incremental-only after backfill.** The nightly job fetches **only `match_id`s not already in the DB**. Never re-scrape a whole season in routine operation.
3. **Respect FBref rate limiting.** Use `soccerdata`'s built-in limiter — never bypass it. Rely on its local cache; never re-request a cached page. This is the difference between a working tool and a blocked IP.
4. **Every match row is scope-tagged.** Both `competition_id` and `competition_type` (`club_league` | `club_cup` | `club_european` | `international`) on every `team_match` and `player_match` row. "Last N games" is meaningless without it.
5. **Squad ≠ lineup.** The fixture page shows **squad form**, never an auto-predicted XI. Confirmed-XI filtering is a manual user step by design.

---

## Data-source rules

**Source-per-scope (strict)** — source per *data type*, not per provider (see `docs/adr/0001`):
- `club_league` team data (event-counts) → **football-data.co.uk**, read via **direct CSV** (`ingestion/fdcouk.py`), not soccerdata — its TLS downloader proved flaky and the spec sanctions direct CSV. No rate limit, deep history, league-only.
- `club_cup` / `club_european` / `international` team data → **FBref** (football-data.co.uk does NOT cover cups).
- **All** player event-count data, every scope → **FBref** (shots, SoT, tackles, fouls, cards, minutes, goals, assists).
- **xG (team + player)** → **deferred out of v1**; `team_match.xg` / `player_match.xg` stay NULL (see `docs/adr/0002`). FBref lost xG (Jan 2026); soccerdata 1.9.0 removed FotMob. Documented future path: Understat, **Premier League only** (top-5 leagues), ingestion-layer only.
- **Finished-match `game_id`s** (player pipeline) → **FBref `read_schedule`** (its `fbref_match_id` aligns with the match-stat pages, keeping incremental dedup clean). **Upcoming scheduled Fixtures** → **ESPN scoreboard JSON** (`ingestion/upcoming.py`, re-run at intervals; display-only, never a stats source; see `docs/adr/0009`).

**v1 scope:** top 4 English tiers (Premier League, Championship, League One, League Two), `club_league` only. Team data confident across all four; player data best-effort + labelled for League One / Two.

**Do not hardcode league codes.** Resolve them via `soccerdata`'s `available_leagues()`.

**FBref stat_type → metric mapping** (use these, don't rediscover):
- **`summary` is the ONLY stat_type fetched — one fetch per match, never per player.**
  It carries everything ingested: `Gls`, `Ast`, `Sh`, `SoT`, `TklW`, `Fld`, `Fls`,
  `CrdY`, `CrdR`, `Min` (see `players._STAT_COLS`). `defense`/`misc` are NOT
  fetched — do not add fetches for tackles or fouls, they are already in `summary`.
- `tackles` = **`TklW` (tackles WON)** — FBref dropped total `Tkl`.
- **Absent from `summary`, by source:** `xg`/`npxG` (FBref removed Jan 2026) and
  `second_yellows`. xG stays NULL — deferred out of v1 per `docs/adr/0002`; FotMob
  is NOT an option (soccerdata 1.9.0 removed it).
- One match page = both squads, all players.
- **Payload varies per match.** Some pages return rows with `Min`/`Gls`/cards but
  NULL `Sh`/`SoT`/`TklW`/`Fls`. It is a per-PAGE property, all-or-nothing: those
  five columns are always NULL together, and of 10,617 fixtures 9,253 are fully
  populated, 1,364 fully NULL, **none mixed**. So a NULL means the source did not
  publish the column — never that the player registered zero. NOT a lower-league
  or condensed-format effect: **95.6% is minor-confederation internationals**
  (WC Qualifiers 890, AFCON Qualifying 374, Asian Cup Qualifying 40), the
  sparsity ADR 0011 accepts by design; the 34 FA Cup fixtures (R3 weekends, Jan
  2024 + Jan 2025, including Arsenal–Man United) are 2.5% of it. Expect NULLs;
  never assume uniformity. Query-side consequence: every aggregate divides by
  **Recorded Appearances**, see `docs/adr/0016`.

**Cross-source reconciliation:** team/player names differ between FBref, football-data.co.uk, ESPN, and FotMob ("Man Utd" vs "Manchester United"). Resolve source names → canonical `id` via the `fbref_id` / `fdcouk_name` / `espn_id` / `fotmob_id` columns in the ingestion step. Never join on raw display strings.

---

## Data-model conventions

- Fact tables `team_match` / `player_match` are **one row per entity per match**. The per-game row IS the breakdown; the rolling headline is a `SUM`/`COUNT` over rows. Don't store rolling features.
- Denormalise `date` and `competition_id` onto fact tables for fast window queries.
- Store derived booleans where trivial (`clean_sheet`, `btts`, `result`).
- **Rolling window bound — support both:**
  - Display/form view → `ROWS BETWEEN (N-1) PRECEDING AND CURRENT ROW` (inclusive of latest).
  - Prediction "going-in" view → `ROWS BETWEEN N PRECEDING AND 1 PRECEDING` (excludes current match; no leakage).
- Compute rolling windows at query time (SQL window functions). Materialise only if a real perf problem shows up — don't pre-optimise.

---

## Code conventions

- Migrations for all schema changes — never mutate the DB by hand. (Alembic.)
- Ingestion, API, and UI are separate concerns; ingestion logic never imported into request handlers.
- Type hints on Python; Pydantic models for API I/O.
- Keep ingestion idempotent — re-running a job must upsert, never duplicate.
- Secrets/config via env, not committed.

---

## Commands

> Fill these in as the project takes shape; keep this section current — it's the first place to look.

All backend commands run from `backend/` using its venv (`.venv`). Activate with
`.venv\Scripts\Activate.ps1` (PowerShell) or prefix with `.venv/Scripts/python.exe`.

```bash
# setup (once)
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
# then: copy .env.example -> .env and set DATABASE_URL

# db (migrations)
.venv/Scripts/python.exe -m alembic upgrade head            # apply
.venv/Scripts/python.exe -m alembic upgrade head --sql      # render SQL offline (no DB)
.venv/Scripts/python.exe -m alembic revision -m "msg"       # new migration
.venv/Scripts/python.exe -m alembic downgrade -1            # roll back one

# reference data (Phase 2 — idempotent, re-runnable)
.venv/Scripts/python.exe -m ingestion.config_sync          # sync league_dict -> ~/soccerdata
.venv/Scripts/python.exe -m ingestion.seed_competitions    # 4 competitions
.venv/Scripts/python.exe -m ingestion.teams                # canonical teams from football-data.co.uk

# team-match data (Phase 3 — football-data.co.uk, idempotent)
.venv/Scripts/python.exe -m ingestion.team_match           # backfill team_match (6 seasons x 4 leagues)

# FAILURE DIGEST (replaces the modal popup, removed 2026-08-30)
# notify_failure.ps1 BLOCKED, so cmd.exe returned 0 and Task Scheduler's Last
# Run Result read "success" while a modal sat unanswered. It also fired PER RUN,
# and `upcoming` runs up to 15x a day (5 slots x 2 retries) -- one standing fault
# became fifteen identical popups (six on 2026-08-27, for an EFL Cup tie that had
# merely not been drawn yet). Now: no popups; Last Run Result is truthful; ask.
.venv/Scripts/python.exe -m ingestion.digest        # last 24h across all 4 logs
.venv/Scripts/python.exe -m ingestion.digest 336    # last 14 days
# AUTOMATED: task "BetStats digest" runs backend/run_digest.cmd daily 09:30
# (after squads 09:00, so the whole day is in) -> backend/logs/digest.txt, which
# is utf-8-SIG so Windows tools render club names and em-dashes correctly.
# Identical causes are COLLAPSED with a count, so a standing fault is one line.

# quality
.venv/Scripts/python.exe -m pytest                         # tests

# dev
# WARNING: --reload does NOT reliably reload here — this repo lives under OneDrive,
# whose virtual filesystem breaks watchfiles, so --reload silently serves STALE code.
# Run plain uvicorn and restart it manually after editing backend code.
.venv/Scripts/python.exe -m uvicorn app.main:app   # API at /docs (CORS allows :5173)
# frontend dev server (Phase 7 — Vite + React + TS + Tailwind, in ../frontend):
#   cd frontend && npm install && npm run dev -- --port 5173   # UI at http://localhost:5173
#   reads VITE_API_BASE (default http://localhost:8000); copy frontend/.env.example -> .env to override

# player-match data (Phase 4 — FBref, VPN OFF, persistent headful session)
.venv/Scripts/python.exe -m ingestion.run_backfill 2526 "Premier League"   # SEASON BACKFILL (watchdog auto-restarts on Cloudflare stall) — use this
.venv/Scripts/python.exe -m ingestion.run_backfill 2324 "Championship"     # competition arg is optional (defaults to Premier League); must be in players.LEAGUE_IDS
.venv/Scripts/python.exe -m ingestion.players 2526 "Premier League" 2      # bounded smoke test (first 2 matches; no watchdog)
# (players.py alone has no per-fetch timeout — a Cloudflare re-challenge can hang it; run_backfill supervises it)

# team identity (Phase A — ADR 0007, zero-network, idempotent)
.venv/Scripts/python.exe -m ingestion.backfill_team_fbref_id   # populate teams.fbref_id from cached FBref match pages

# cup team_match (ADR 0008 follow-up — zero-network, idempotent; run AFTER the cup player backfill)
.venv/Scripts/python.exe -m ingestion.cups team 2425 "FA Cup"  # 2 team_match rows/fixture from cached scorebox + team_stats_extra corners + player-row sums; also accepts "Championship Play-offs"

# upcoming fixtures (ADR 0009 — ESPN scoreboard, display-only; idempotent)
.venv/Scripts/python.exe -m ingestion.upcoming 45              # forward window in days (~1 request/league)
# ALSO CARRIES THE DOMESTIC CUPS (ADR 0012). FA/EFL Cup take FINISHED events too
# ("finished" is a SET: STATUS_FULL_TIME / _FINAL_PEN / _FINAL_AET — cups go to
# ET and pens), and their window reaches 30 days BACK, because a played tie is in
# the past and a forward-only window steps straight over last night's game. That
# finished row is what makes matchday able to SEE a cup round at all. Cups run
# LAST and never roll back the slate on an unresolved name — they exit 1 instead.
# Only Covered ties are kept, so the non-league early rounds are never alias work.
# ADR 0014: ALSO MARKS LEAGUE FIXTURES FINISHED. `finished` means the match was
# PLAYED, not "we have the results" — before this only fd.co.uk marked a league
# fixture finished, so an unpublished CSV read as "never happened" and silently
# withdrew it from FBref's player pipeline too (cost 6 PL + 11 Champ fixtures,
# Aug 2026). Internationals stay scheduled-only ON PURPOSE: their placeholders
# are purged by status='scheduled', so marking one finished strands it forever.
# Also runs the STALLED backstop: a fixture long past kick-off that nothing
# marked finished, and that ESPN did not report postponed -> exit 1.
# AUTOMATED: Windows Task Scheduler task "BetStats upcoming fixtures" runs
# backend/run_upcoming.cmd daily 07:30 (catches up after missed starts) ->
# backend/logs/upcoming.log — check that log first if the slate looks stale.
# fails loud on unknown ESPN names: extend ESPN_TEAM_ALIASES; a NEWLY PROMOTED
# (ex-National-League) club must be seeded deliberately first — summer-prep step
# World Cup slate: international placeholders are EPHEMERAL (August-boundary
# season, purged once kicked off — FBref ingest owns the finished row); events
# with an undecided side ("Semifinal 1 Winner") are held until both slots resolve

# internationals (ADR 0011 — finals + NL + qualifiers; whole-competition, NO covered filter)
# Arg 1 is the soccerdata FETCH EDITION ("2022" WC; "2223" NL two-year code), NOT the
# stored season — that is derived per MATCH DATE, AUGUST boundary (Euro 2024 -> '2324').
# Arg 2 is a LEAGUE_IDS SELECTOR: the "WC Qual <confed>"/"WC Qual Play-offs" selectors
# all store into ONE "World Cup Qualifiers" competition (team mode takes THAT row name).
# Qualifiers ride ingestion/fbref_shim.py (h2-heading fallback; qualifier history pages
# have no table#seasons); matches before 2020-08-01 skip at ingest (dates rule).
# CHAIN ORDER RULE: "WC Qual AFC" BEFORE "Asian Cup Qualifying" — dual-badged matches
# land under the WC label (CONTEXT.md: Dual-badged match). Nation spelling trips the
# fail-loud resolver by design: add FBREF_TEAM_ALIASES (player-df -> schedule) + re-run.
.venv/Scripts/python.exe -m ingestion.run_backfill 2022 "World Cup"      # player pass (VPN OFF, watchdog)
.venv/Scripts/python.exe -m ingestion.run_backfill 2026 "WC Qual UEFA"   # a qualifier league, same path
.venv/Scripts/python.exe -m ingestion.internationals team "World Cup Qualifiers"  # team rows (zero-network, loops stored seasons)

# squad membership (ADR 0013 — ESPN rosters; TIER 1, unattended, idempotent)
# Membership only, NEVER a stat — every Metric still comes from FBref. No VPN, no
# headful browser, no rate limiter. ~92 requests (the four English tiers), ~35s.
.venv/Scripts/python.exe -m ingestion.squads
# AUTOMATED: Task Scheduler task "BetStats squads" runs backend/run_squads.cmd
# daily 09:00 (StartWhenAvailable) -> backend/logs/squads.log.
# ORDER IS LOAD-BEARING: it must run AFTER "BetStats upcoming fixtures" (07:30),
# because _rostered_teams() derives its club list from the Fixture slate — a
# promoted/relegated club only follows the season once upcoming has laid this
# season's fixtures down. 09:00 also puts it after matchday (08:00), so the
# identity ladder sees the freshest player names; preferred, not required.
# Membership = Squad ∪ anyone with an appearance in the last 30 days, so an
# unmatched name degrades the panel to SLIGHTLY STALE, never to missing a player.
# Exit 1 = a club's roster fetch failed -> that Squad is left STALE and the panel
# shows it without complaint, hence the digest (below).
# UNMATCHED NAMES ARE NORMAL (~519 of 2605, heavily League One/Two where we hold
# little player data) — they surface in the panel as members with "—". Only add
# an ESPN_PLAYER_ALIASES entry when it is a player we actually HOLD and the
# deterministic ladder cannot reach him (e.g. an ESPN typo, or a nickname).

# points deductions (ADR 0010 — ESPN standings; feeds the computed GET /table)
.venv/Scripts/python.exe -m ingestion.points_adjustments          # dry-run: print found deductions
.venv/Scripts/python.exe -m ingestion.points_adjustments --apply  # upsert (idempotent; re-run for new rulings)

# nightly incremental (Phase 5 — TWO TIERS; incremental by design, never full-season)
#
# TIER 1 — UNATTENDED (team data + points): safe headless, no Cloudflare/rate limit.
.venv/Scripts/python.exe -m ingestion.nightly     # current-season fd.co.uk team data + ESPN points
# AUTOMATE like upcoming: Task Scheduler task running backend/run_nightly.cmd
# (StartWhenAvailable) -> backend/logs/nightly.log. Idempotent; catch-up-safe.
#
# TIER 2 — SUPERVISED (FBref player data): headful, VPN OFF, machine awake, watchdog.
.venv/Scripts/python.exe -m ingestion.matchday                       # EVERY comp w/ pending work (default)
.venv/Scripts/python.exe -m ingestion.matchday "FA Cup" "Champions League"  # exactly these, order kept
# (or run_matchday.cmd — sets PYTHONIOENCODING=utf-8 for foreign names). Wraps
# run_backfill per comp, builds cup/European team rows (zero-network), then sweeps
# uc_driver.exe orphans a clean exit leaks. UEFA Super Cup excluded (deferred).
# ADR 0012: the default run is NO LONGER leagues-only — it ingests any cup or
# European round that has been played. Pending work is found two ways: domestic
# cups via the same zero-network DB probe the leagues use (their fixtures now
# arrive from ESPN already finished), Europe via ESPN directly (signal only, no
# rows written, main slugs only — never uefa.*_qual, which FBref does not carry).
# A skipped tie now exits 1 -> shows in the digest. NOT a skip if dropped on IDENTITY:
# FBref's cup schedule gives names only, and the non-league "Bournemouth"/
# "Liverpool" read as the PL clubs, so coverage is re-checked against the match
# page's real team ids and collisions are dropped quietly.
#
# COVERAGE AUDIT (ADR 0014 — ingestion/coverage.py; zero-network, read-only)
# Answers "has each source published this fixture's data?" PER FIXTURE PER
# SOURCE. The old check was per LEAGUE-SEASON and so could only see "this league
# published nothing" — when fd.co.uk published 12 of 23 Championship games the
# other 11 were invisible. Two tiers: OVERDUE (past that source's grace, recent)
# ALARMS; older becomes a standing known-gaps COUNT that never alarms (~79
# minor-nation internationals FBref seems not to publish lineups for).
# What each source owes is read from the ingesters' OWN constants, never a
# second list — so L1/L2's 6,649 player-less fixtures are correctly NOT gaps.
# Each job audits the source it owns, so an alarm never misattributes:
#   nightly  -> fd.co.uk team rows   (alongside unexpected_skips, which asks the
#               different question "did the FETCH return anything at all")
#   matchday -> FBref player rows    (INCLUDING on the "nothing to do" path —
#               an empty plan is a claim, and the audit checks it)
# Grace is PROVISIONAL (fd.co.uk 48h, FBref 24h): retune from real reports.
#   .venv/Scripts/python.exe -c "import datetime as dt; from app.db import SessionLocal; from ingestion import coverage as c; s=SessionLocal(); [c.audit(s,x,now=dt.datetime.now(dt.timezone.utc)) for x in (c.TEAM_FDCOUK,c.PLAYER_FBREF)]"

# SEASON ROLLOVER (do BEFORE the first 2627 match day):
#   -> 2026-27 has a DATED working copy with steps 1-3 already checked off
#      against the DB: docs/season-rollover-2627.md. Read that first.
#   1. Seed any newly-promoted ex-National-League club (upcoming.py fails loud on
#      unknown ESPN names — see the upcoming note above).
#   2. Run tier 1 once so 2627 PL/Championship team_match exists — this is what
#      makes cups.covered_team_ids("2627") resolve the new promoted/relegated set
#      (it is season-driven, not hardcoded) before any cup tie is filtered.
#   3. New foreign clubs in Europe trip the fail-loud alias guard by design:
#      add the alias + re-run, not a bug (see memory cups-internationals-sourcing).
#   4. REFRESH THE CACHED SEASON INDEX -- the once-a-year trap. soccerdata caches
#      ~/soccerdata/data/FBref/seasons_<league>.html, and a copy taken in the
#      off-season does NOT list the new season, so EVERY FBref read dies with
#      KeyError('<season>') until it is refreshed. It presents as matchday
#      exiting 1 daily. Fix = delete the stale file(s) and re-run supervised so
#      soccerdata re-fetches; ~25s per league:
#        rm "~/soccerdata/data/FBref/seasons_ENG-Premier League.html"   (+ Championship,
#        FA Cup, EFL Cup -- check each with: grep -oE "20[0-9]{2}-20[0-9]{2}" <file> | tail -1)
#      This is a DELIBERATE exception to "never re-request a cached page": the
#      page is stale, not valid. European comps are usually already fine (UEFA
#      publishes earlier). Hit 2026-08-19; cost 5 days of Championship data.
```

---

## Do NOT

- Do NOT scrape at request time.
- Do NOT bypass `soccerdata`'s rate limiter or cache.
- Do NOT re-scrape full seasons in the nightly job.
- Do NOT hardcode `soccerdata` league codes.
- Do NOT join across sources on display names.
- Do NOT auto-fetch predicted lineups (out of scope; unreliable free data).
- Do NOT add multi-user/auth/public-hosting concerns (single-user by design).
- Do NOT ingest, store, or model **odds** — odds are fully out of scope (a "Market" is a bookmaker offering; we don't touch it). See `CONTEXT.md`.

---

## When in doubt

- Prefer the boring, reliable ingestion path over a clever one. Pipeline correctness > UI polish; build the pipeline first.
- If "last N" looks wrong, check ingested **scope** before suspecting the query.
- Surface coverage honestly in the UI ("covered competitions only") rather than implying completeness — especially for internationals.
- Flag, don't silently work around, any source that changes shape or starts rate-limiting differently.
