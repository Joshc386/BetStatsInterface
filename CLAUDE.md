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
- **Schedule / upcoming Fixtures** → **FBref `read_schedule`** (its `fbref_match_id` aligns with the match-stat pages, keeping incremental dedup clean). ESPN is fallback only.

**v1 scope:** top 4 English tiers (Premier League, Championship, League One, League Two), `club_league` only. Team data confident across all four; player data best-effort + labelled for League One / Two.

**Do not hardcode league codes.** Resolve them via `soccerdata`'s `available_leagues()`.

**FBref stat_type → metric mapping** (use these, don't rediscover) — FBref no longer provides **xG/npxG** (removed Jan 2026); source xG from FotMob:
- `summary` → shots, shots on target, cards (NOT xG anymore)
- `defense` → tackles
- `misc` → fouls drawn (`Fld`), fouls committed (`Fls`), cards, offsides
- One match page = both squads, all players. Multiple stat-types for one match = one cached fetch per type, not per player.

**Cross-source reconciliation (three-way):** team/player names differ between FBref, football-data.co.uk, and FotMob ("Man Utd" vs "Manchester United"). Resolve source names → canonical `id` via the `fbref_id` / `fdcouk_name` / `fotmob_id` columns in the ingestion step. Never join on raw display strings.

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

# quality
.venv/Scripts/python.exe -m pytest                         # tests

# dev
.venv/Scripts/python.exe -m uvicorn app.main:app --reload   # API at /docs (CORS allows :5173)
# frontend dev server (Phase 7 — Vite + React + TS + Tailwind, in ../frontend):
#   cd frontend && npm install && npm run dev -- --port 5173   # UI at http://localhost:5173
#   reads VITE_API_BASE (default http://localhost:8000); copy frontend/.env.example -> .env to override

# player-match data (Phase 4 — FBref, VPN OFF, persistent headful session)
.venv/Scripts/python.exe -m ingestion.run_backfill 2526 "Premier League"   # SEASON BACKFILL (watchdog auto-restarts on Cloudflare stall) — use this
.venv/Scripts/python.exe -m ingestion.run_backfill 2324 "Championship"     # competition arg is optional (defaults to Premier League); must be in players.LEAGUE_IDS
.venv/Scripts/python.exe -m ingestion.players 2526 "Premier League" 2      # bounded smoke test (first 2 matches; no watchdog)
# (players.py alone has no per-fetch timeout — a Cloudflare re-challenge can hang it; run_backfill supervises it)

# ingestion  (TODO — Phase 5)
# nightly incremental / roster refresh
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
