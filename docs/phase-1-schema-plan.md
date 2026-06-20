# Phase 1 — Schema & Migrations Plan

> **Status: APPLIED (2026-06-20).** ORM models + migration `0001` written, applied to the
> `betstats` database, and verified live (7 tables, generated columns compute correctly,
> `alembic check` clean). The four open decisions were all taken as recommended (yes).
> This plan supersedes §7 of `betting-research-platform-scope.md`.

## Approach

- **Alembic**, single initial migration: create enum types → reference tables → fact tables → indexes.
- **Idempotent ingestion** via `INSERT … ON CONFLICT (<natural key>) DO UPDATE` — re-running upserts, never duplicates. One `team_match` row may be assembled from *several* sources (football-data.co.uk base + FotMob xG), so upsert merges columns onto the same row.
- **Derived booleans are Postgres `GENERATED ALWAYS … STORED` columns** — `clean_sheet`, `btts`, `result`, `carded`. They can't drift from their inputs and need no ingestion logic.
- All money/odds concepts absent (out of scope). No `match_odds` table.

## Enum types

- `competition_type` = `club_league` | `club_cup` | `club_european` | `international`
- `fixture_status` = `scheduled` | `finished`
- `match_result` = `W` | `D` | `L`

## Reference tables

### `competitions`
| column | type | notes |
|---|---|---|
| `id` | int identity PK | |
| `name` | text not null | |
| `type` | `competition_type` not null | |
| `country` | text | |
| `tier` | smallint null | English pyramid level (1–4); orders/labels the UI |
| `fbref_key` | text | source id |
| `fdcouk_key` | text | source id (e.g. `E0`,`E1`,`E2`,`E3`) |
| `fotmob_id` | text | source id (for xG) |

### `teams`
| column | type | notes |
|---|---|---|
| `id` | int identity PK | canonical |
| `canonical_name` | text not null | |
| `country` | text | |
| `fbref_id` | text unique | reconciliation glue |
| `fdcouk_name` | text | reconciliation glue |
| `fotmob_id` | text unique null | reconciliation glue (xG) |
| `espn_id` | text null | schedule fallback only |

### `players`
| column | type | notes |
|---|---|---|
| `id` | int identity PK | canonical |
| `canonical_name` | text not null | |
| `fbref_id` | text unique | reconciliation glue |
| `fotmob_id` | text null | **reserved for deferred player-xG path** (open decision Q1) |
| `current_team_id` | int FK→teams null | live roster pointer (mutable) |
| `nationality` | text | |
| `position` | text | |

## Schedule

### `fixtures`  (both upcoming and finished — one row per event)
| column | type | notes |
|---|---|---|
| `id` | int identity PK | |
| `competition_id` | int FK→competitions not null | |
| `season` | text not null | soccerdata convention, e.g. `2526` |
| `date` | timestamptz not null | kickoff |
| `home_team_id` | int FK→teams not null | |
| `away_team_id` | int FK→teams not null | |
| `status` | `fixture_status` not null | flips `scheduled`→`finished` on result ingest |
| `fbref_match_id` | text null | from FBref `read_schedule`; dedup key |
| `fdcouk_ref` | text null | football-data.co.uk reconciliation |

- **Unique** `(fbref_match_id)` where not null.
- **Unique** `(competition_id, season, home_team_id, away_team_id)` — natural key to reconcile FBref schedule ↔ football-data.co.uk results when source ids differ.

## Fact tables (one row per entity per Fixture)

Both carry **`competition_id` AND `competition_type`** (non-negotiable #4) plus denormalised `season` and `date` for fast scope-filtered window queries.

### `team_match`
| column | type | notes |
|---|---|---|
| `id` | int identity PK | |
| `fixture_id` | int FK→fixtures not null | |
| `competition_id` | int FK not null | denormalised |
| `competition_type` | `competition_type` not null | denormalised (scope filter) |
| `season` | text not null | denormalised |
| `date` | timestamptz not null | denormalised |
| `team_id` | int FK→teams not null | |
| `opponent_id` | int FK→teams not null | |
| `is_home` | bool not null | |
| `gf`,`ga` | smallint null | |
| `shots`,`sot`,`shots_conceded`,`sot_conceded` | smallint null | |
| `fouls`,`corners`,`yellows`,`reds` | smallint null | |
| `xg` | numeric(5,2) null | FotMob; null where uncovered (e.g. L1/L2) |
| `clean_sheet` | bool GENERATED `(ga = 0)` STORED | |
| `btts` | bool GENERATED `(gf > 0 AND ga > 0)` STORED | |
| `total_goals` | smallint GENERATED `(gf + ga)` STORED | drives over/under thresholds |
| `result` | `match_result` GENERATED (CASE on gf/ga) STORED | |

- **Unique** `(fixture_id, team_id)` — upsert key.

### `player_match`  (one row = one Appearance, minutes > 0)
| column | type | notes |
|---|---|---|
| `id` | int identity PK | |
| `fixture_id` | int FK→fixtures not null | |
| `competition_id` | int FK not null | denormalised |
| `competition_type` | `competition_type` not null | denormalised |
| `season` | text not null | denormalised |
| `date` | timestamptz not null | denormalised |
| `player_id` | int FK→players not null | |
| `team_id` | int FK→teams not null | team at match time (immutable history) |
| `opponent_id` | int FK→teams not null | |
| `is_home` | bool not null | |
| `minutes` | smallint not null | >0 by definition; distinguishes start vs cameo |
| `shots`,`sot`,`tackles`,`fouls_drawn`,`fouls_committed` | smallint null | |
| `yellows`,`reds`,`second_yellows` | smallint null | `second_yellows` from FBref `2CrdY` (open decision Q2) |
| `xg` | numeric(5,2) null | **NULL in v1** (no source); reserved |
| `carded` | bool GENERATED `(COALESCE(yellows,0)>0 OR COALESCE(reds,0)>0)` STORED | drives "to be booked" hit-rate |

- **Unique** `(fixture_id, player_id)` — upsert key.

### `squads`  (current roster snapshot, refreshed by Job C)
| column | type | notes |
|---|---|---|
| `team_id` | int FK→teams | PK part |
| `player_id` | int FK→players | PK part |
| `active` | bool not null | |
| `last_seen` | date | |

- **PK** `(team_id, player_id)`.

## Indexes (for rolling-window & H2H queries)

- `team_match (team_id, competition_type, date DESC)` — team window within scope
- `team_match (team_id, opponent_id, date DESC)` — head-to-head
- `team_match (team_id, season)` — season window
- `player_match (player_id, competition_type, date DESC)` — player window within scope
- `player_match (player_id, season)` — season window
- `player_match (team_id, date DESC)` — squad-form panel
- `fixtures (date)`, `fixtures (status, date)`, `fixtures (competition_id, season)`

## Open decisions for you

1. **`players.fotmob_id`** — add now (cheap, supports the deferred player-xG path without a later migration) or leave out until that module is built? *Recommend: add now.*
2. **`player_match.second_yellows`** — store now (comes free from FBref `misc`, enables the correct `cards_shown` formula later) or omit until needed? *Recommend: store now.*
3. **`competitions.tier`** — keep this English-pyramid ordering column? *Recommend: yes, cheap and useful for UI.*
4. **Generated columns** for `clean_sheet`/`btts`/`total_goals`/`result`/`carded` — agree, vs computing them in ingestion? *Recommend: generated (can't drift).*

## Not in this phase

Rolling windows, hit-rates, per-90 — all **computed at query time** (Phase 6), never stored. This phase is tables only.
