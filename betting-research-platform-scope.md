# Project Scope — Football Betting-Research Platform

> ⚠️ **PARTIALLY SUPERSEDED (2026-06-19).** This is the original brief. Where it conflicts with the live docs, the live docs win:
> - **Sources / xG / odds** → see `CLAUDE.md` "Data-source rules" and `docs/adr/0001-data-sources-after-fbref-opta-loss.md`. FBref lost xG (Jan 2026); team xG now from **FotMob**; player xG NULL in v1; **odds fully out of scope** (no `match_odds` table).
> - **Domain terms** (Metric / Summary Metric / Threshold / Rolling Window / Appearance) → see `CONTEXT.md`.
> - Data model additions: `teams.fotmob_id`; `season` column on `team_match` / `player_match`.
> - A full reconciliation pass over §5, §7, §8, §11 of this doc is pending before build.

**Purpose of this document:** A build brief to hand to Claude Code. It defines what to build, the data model, the data sources, the ingestion strategy, and a suggested build sequence. It is intentionally prescriptive about architecture and deliberately scoped to free data sources only.

---

## 1. Summary

A **personal, single-user** web application for betting research. It ingests football match data from free sources into a local Postgres database, and lets the user explore **team-level** and **player-level** statistics, filter by **betting market/metric** and by **competition**, view **rolling-window form** (e.g. last N games), and drill into **upcoming fixtures** with squad-level data and head-to-head history.

It is a research/analysis tool — **not** a betting bot and **not** a predictive model. It surfaces the raw and rolled-up numbers the user needs to form their own market reads.

**Single most important architectural rule:** external data sources are **ingestion feeds only**. The frontend and API **never** call FBref / football-data.co.uk / ESPN at request time. Everything the app serves comes from the local Postgres DB, populated by scheduled jobs.

---

## 2. User & usage

- One user (the owner). No auth, no multi-tenancy, no public exposure required (localhost / private host is fine).
- Personal/educational use only. (This matters for data-source terms of service — redistribution is out of scope, so no ToS concerns for FBref/football-data.co.uk under personal use.)

---

## 3. Tech stack

- **Backend:** FastAPI (Python)
- **Database:** PostgreSQL
- **Frontend:** React / Next.js
- **Data ingestion:** `soccerdata` library (wraps FBref, football-data.co.uk, ESPN, Understat, etc.), plus direct CSV pulls where simpler
- **Scheduling:** a nightly job runner (cron, APScheduler, or n8n — user already runs n8n)


---

## 4. Architecture overview

```
[ free sources ]                [ your infra ]                 [ you ]
football-data.co.uk  ──┐
FBref (via soccerdata)─┼──►  ingestion jobs ──►  PostgreSQL ──►  FastAPI  ──►  React UI
ESPN (optional)      ──┘     (scheduled)         (canonical)     (queries)     (browser)
```

- **Ingestion layer:** scheduled jobs pull *new* match data, normalise it, and upsert into Postgres. Sources are touched only here.
- **Storage layer:** Postgres holds canonical match-level rows (one per team-per-match, one per player-per-match) plus reference tables.
- **Serving layer:** FastAPI computes rolling windows and derived markets **at query time** using SQL window functions (optionally materialised views for speed). No scraping in the request path.

---

## 5. Data sources

| Source | Provides | Access | Constraints |
|---|---|---|---|
| **football-data.co.uk** | Team-level **league** match stats: goals, shots, shots on target, fouls, corners, yellows, reds, result, **plus closing odds** | `soccerdata` `MatchHistory` or direct season CSVs | Static files, **no rate limit**. **League competitions only** (no cups/Europe). Deep history. |
| **FBref** (via `soccerdata`) | Player-level per-match stats (shots, SoT, tackles, fouls drawn, fouls committed, cards, minutes); team-level per-match stats; xG; covers **leagues, cups, European comps, internationals** | `soccerdata` `FBref` | **Rate-limited** — respect `soccerdata`'s built-in limiter (~1 request / 6s; treat ~10 req/min as the ceiling). Caches each match page locally. Opta-sourced. |
| **ESPN** (optional) | Clean fixtures/scores feed | `soccerdata` ESPN scraper or `site.api.espn.com` JSON | Optional. Use only if a live results feed is wanted; thin on detailed/disciplinary stats. |
| **Understat** (optional) | Shot-level xG | `soccerdata` Understat | Optional. Top-5 leagues only — **no Championship**. Shooting only. |

**Source-per-scope rule (important):**
- **League** team-level data → football-data.co.uk (richest, no rate limit).
- **Cup / European / international** team-level data → **FBref** (football-data.co.uk does not cover cups).
- **All** player-level data → **FBref** (every competition).

> Action for Claude Code: confirm exact league-code strings via `soccerdata`'s `available_leagues()` rather than hardcoding guesses (e.g. the Championship code).

---

## 6. Scope decision — competitions & taxonomy

The set of competitions ingested defines what "last N games" and "filter by competition" actually mean. Tag **every** match row with both a `competition` and a `competition_type`:

- `club_league` (e.g. Premier League, Championship)
- `club_cup` (e.g. FA Cup, EFL Cup)
- `club_european` (e.g. Champions League, Europa League)
- `international` (e.g. World Cup, Euros, qualifiers, Nations League)

**v1 ingest target (recommended starting set):** Premier League + Championship (`club_league`). Add FA Cup / EFL Cup / European comps as `club_cup` / `club_european` in a later pass. Treat `international` as last and lowest-confidence (coverage is fragmented — friendlies and some qualifiers have gaps; surface "covered competitions only" in the UI rather than implying completeness).

---

## 7. Data model (PostgreSQL)

### Reference tables

**`competitions`**
- `id` (PK)
- `name`
- `type` enum (`club_league` | `club_cup` | `club_european` | `international`)
- `country`
- `fbref_key`, `fdcouk_key` (source identifiers)

**`teams`**
- `id` (PK)
- `canonical_name`
- `fbref_id`, `fdcouk_name`, `espn_id` (for cross-source reconciliation)
- `country`

**`players`**
- `id` (PK)
- `canonical_name`
- `fbref_id`
- `current_team_id` (FK → teams)
- `nationality`
- `position`

**`fixtures`** (the schedule — both finished and upcoming)
- `id` (PK)
- `competition_id` (FK)
- `date` (timestamp)
- `home_team_id`, `away_team_id` (FK → teams)
- `status` enum (`scheduled` | `finished`)
- `fbref_match_id`, `fdcouk_ref` (source match identifiers, for incremental dedup)

### Core fact tables

**`team_match`** — one row per team per match
- `id` (PK)
- `fixture_id` (FK), `competition_id` (FK)
- `team_id`, `opponent_id` (FK → teams)
- `is_home` (bool)
- `date` (denormalised for fast window queries)
- Metrics: `gf`, `ga`, `shots`, `sot`, `shots_conceded`, `sot_conceded`, `fouls`, `corners`, `yellows`, `reds`, `xg` (nullable)
- Derived/stored booleans for convenience: `clean_sheet` (`ga = 0`), `btts` (`gf > 0 AND ga > 0`), `result` (`W`/`D`/`L`)

**`player_match`** — one row per player per match
- `id` (PK)
- `fixture_id` (FK), `competition_id` (FK)
- `player_id`, `team_id`, `opponent_id` (FK)
- `is_home` (bool)
- `date` (denormalised)
- Metrics: `minutes`, `shots`, `sot`, `tackles`, `fouls_drawn`, `fouls_committed`, `yellows`, `reds`, `bookings` (`yellows + reds`), `xg` (nullable)

**`match_odds`** (optional but recommended — comes free in the football-data.co.uk CSV)
- `fixture_id` (FK)
- Closing odds columns: `b365_home`, `b365_draw`, `b365_away`, `over25`, `under25`, etc.
- Lets the user compare their market read against priced odds. **Store only — no modelling in v1.**

**`squads`** — current roster per club (periodic refresh)
- `team_id` (FK), `player_id` (FK), `active` (bool), `last_seen` (date)

> Cross-source reconciliation: team/player names differ between FBref and football-data.co.uk ("Man Utd" vs "Manchester United"). The `fbref_id` / `fdcouk_name` columns on `teams`/`players` are the glue. Build a small mapping step in ingestion that resolves source names → canonical `id`.

---

## 8. Markets / metrics catalogue

These are the filterable "markets" the UI exposes. Each is just a column (or simple derivation) on a fact table.

**Player-level** (`player_match`):
- Shots, Shots on target
- Tackles
- Fouls drawn, Fouls committed
- Yellow cards, Bookings (yellows + reds)
- Minutes played
- xG (where available)

**Team-level** (`team_match`):
- Goals for, Goals against
- Shots, Shots on target
- Shots conceded, SoT conceded
- Fouls, Corners
- Yellow cards, Red cards, Total cards
- Clean sheets (derived)
- BTTS (derived)
- Over/Under goals at thresholds (derived: `gf + ga > X`)
- xG (where available)

---

## 9. Derived views & query patterns

Nothing below is stored as a feature — all computed from the fact tables.

**Rolling window (last N games)** — SQL window function over the entity's date-ordered rows:

```sql
-- Player: bookings in the last 7 games, per competition scope
SELECT date, opponent_id,
       SUM(bookings) OVER (
         PARTITION BY player_id, competition_id
         ORDER BY date
         ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
       ) AS bookings_last_7
FROM player_match
WHERE player_id = :player_id;
```

**Window-bound convention (build both):**
- **Display / form view** → `ROWS BETWEEN (N-1) PRECEDING AND CURRENT ROW` (the N most recent completed games, inclusive).
- **Prediction-style "going in" view** → `ROWS BETWEEN N PRECEDING AND 1 PRECEDING` (excludes the current match — avoids leakage if ever feeding a model).

**Per-game breakdown = the base rows themselves.** Always return both the rolling headline *and* the underlying per-game rows (date, opponent, value). The breakdown is not extra work — it's the source data the headline aggregates.

**Head-to-head** = `team_match` filtered to the two team_ids facing each other.

**BTTS / clean sheet / over-under** = derived booleans/thresholds over `team_match` (stored where trivial, e.g. `clean_sheet`, `btts`).

---

## 10. Pages / features

**Dashboard / search**
- Search for a team or player; jump to their view.

**Player view**
- Select player → select market(s) → select rolling window N → **filter by competition / competition_type**.
- Shows: rolling headline (e.g. "tackled in 5 of last 5") **and** the per-game breakdown table (date, opponent H/A, value).
- Split by scope (club_league / cup / european / international) as tabs or a filter.

**Team view**
- Same pattern for team markets (clean sheets, cards, BTTS, corners, shots conceded, etc.) over last N, with per-game fixture breakdown and competition filter.

**Fixture view** (the centrepiece) — for an upcoming fixture, e.g. Arsenal vs Chelsea:
- **Squad-form panel** for *both* squads: every player in the current roster with recent minutes, showing their rolling market numbers. **This is a squad-form view, not a predicted XI.** When the official lineup is released, the user manually filters this panel to the confirmed starters.
- **Team form panel**: each team's recent team-level markets (shots conceded, BTTS rate, card rate, clean sheets, etc.) over the chosen window.
- **Head-to-head panel**: historical meetings between the two teams with key markets.
- All panels respect a **competition filter** so the user can scope the form to, e.g., league-only.

---

## 11. Ingestion jobs

**Job A — Historical backfill (run once)**
- For each ingested competition + season: pull schedule, then player-match and team-match stats.
- This is the only heavy operation (hours, due to FBref rate limits). Run once, cache everything.

**Job B — Incremental nightly update (the routine job)**
- **Rule: fetch only `match_id`s not already in the DB. Never re-scrape a whole season.**
- football-data.co.uk: re-pull the current-season CSV and **upsert** (cheap, no limit).
- FBref: pull only the new finished matches' stat-types (`summary`, `defense`, `misc` at minimum — see below) and upsert.
- A typical English matchday is ~10 PL + ~12 Championship matches → ~22 new fetches → a few minutes under the rate limit.

**Job C — Roster refresh (weekly, and after transfer windows)**
- Pull current squad pages from FBref to keep `squads` / `players.current_team_id` accurate.

**FBref stat_type → metric mapping** (for the ingestion code):
- `summary` → shots, shots on target, cards, xG
- `defense` → tackles
- `misc` → fouls drawn (`Fld`), fouls committed (`Fls`), cards, offsides
- (One match page covers both squads, all players — so multiple stat-types for one match still cost one cached fetch per type, not per player.)

**Rate-limit discipline (non-negotiable):**
- Use `soccerdata`'s built-in limiter; do not bypass it.
- Rely on `soccerdata`'s local cache; never re-request cached pages.
- Incremental-only after the initial backfill.

---

## 12. Out of scope (v1) / future

- **Predicted lineups** — not auto-fetched. User applies the official XI manually once released. (Free predicted-lineup data is unreliable.)
- **Live / in-play** data.
- **Multi-user, auth, public hosting.**
- **Odds modelling / value detection** — odds are *stored* but not modelled in v1.
- **Championship xG via Understat** — not available; rely on FBref xG where present, leave nullable otherwise.
- **International competitions** — defer; coverage is fragmented. Surface "covered competitions only" when shown.

---

## 13. Suggested build sequence

1. **Schema + migrations** — stand up the Postgres tables in Section 7.
2. **Source reconciliation** — `competitions`/`teams`/`players` reference data + the name→id mapping step.
3. **Ingestion: football-data.co.uk (team, league)** — simplest source, no rate limit; get `team_match` + `match_odds` populated for PL + Championship.
4. **Ingestion: FBref (player)** — backfill `player_match`; respect rate limits; verify stat_type mapping.
5. **Incremental nightly job (Job B)** — get the update cadence working before building much UI.
6. **API: rolling-window + breakdown endpoints** — team and player, with competition filter and configurable N (both window-bound conventions).
7. **UI: player view & team view** — headline + per-game breakdown + filters.
8. **UI: fixture view** — squad-form + team-form + H2H panels.
9. **Roster refresh job (Job C).**
10. **(Optional) ESPN live-fixtures feed; cup/European/international scopes.**

---

## 14. Key risks / gotchas to respect

- **"Last N" completeness depends on ingested scope.** If only the league is ingested, "last N" silently means "last N league games." Tag scope and be explicit in the UI.
- **FBref rate limiting** is the main operational constraint. Incremental-only + caching keeps it trivial; naive re-scraping gets the IP blocked.
- **Cross-source name mismatches** between FBref and football-data.co.uk — handle in the reconciliation step, not ad hoc.
- **Cups/Europe team-data must come from FBref**, not football-data.co.uk (which is league-only).
- **International / country data is the weakest** — fragmented coverage; treat as best-effort and label it.
- **Squad ≠ lineup.** The fixture page shows squad form; confirmed-XI filtering is a manual step by design.
```
