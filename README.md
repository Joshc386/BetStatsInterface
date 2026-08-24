# BetStats

A personal football betting-research tool. Scheduled jobs ingest free football data into a local PostgreSQL database; a read-only FastAPI serves rolling-window team and player statistics to a React interface.

The question it is built to answer is the one a bettor actually asks before a match: *how often has this happened recently, and over which games?* Rather than quoting an average, it reports a hit rate against a threshold the user chooses — "carded in 4 of his last 5 league games" — and shows the per-game rows that produce it, so the headline and the evidence always reconcile.

It is a research tool. It is **not** a betting bot, **not** a predictive model, and it does **not** ingest, store, or reason about bookmakers' odds.

---

## What it holds

Current contents of the database (August 2026):

| Table | Rows |
|---|---|
| Fixtures | 17,471, across 21 competitions |
| Team-match | 34,080 |
| Player-match | 313,001 |
| Players | 17,087 |
| Teams | 437 |

Seasons run from 2020-21 to the current 2026-27.

Coverage is scoped deliberately rather than opportunistically:

- **Leagues** — the top four English tiers (Premier League, Championship, League One, League Two). Team data is complete across all four; player data is confident for the Premier League and Championship and best-effort below that, where the underlying source has always been thin. The interface labels it accordingly rather than implying completeness.
- **Domestic cups** — FA Cup, EFL Cup and the promotion play-offs, for ties involving at least one Premier League or Championship club that season.
- **Europe** — Champions League, Europa League and Conference League, on the same covered-tie rule.
- **Internationals** — the major finals (World Cup, Euros, Copa América, AFCON, Asian Cup, Gold Cup), their qualifying campaigns and the Nations League. Held for continuity in a player's appearance history, not for fixture research. Friendlies are excluded permanently.

Expected shot data (xG) is deferred and stored as NULL. FBref lost its Opta feed in January 2026, and the alternative sources were judged worse than an honest gap; see [ADR 0002](docs/adr/0002-xg-deferred-fotmob-removed.md).

---

## What the interface does

- **Landing page** — search for a team or player, above a slate of the next twenty scheduled fixtures grouped by day.
- **Fixture view** — two teams side by side: each side's recent form, their head-to-head meetings, and a squad-form panel for both squads.
- **Team hub** — one club's full breakdown, with venue, competition and season filters.
- **Player view** — a player's appearance history, segmented by club spell or by competition, with per-appearance and per-90 figures alongside the hit rate.
- **League table** — computed from the stored match rows and any points deductions, never fetched from a provider.

Every headline is a **Summary Metric**: one metric, over one rolling window, within one competition scope. Windows are selectable as "last N games" or by season, and can be read inclusively (display) or excluding the current match (going-in, for leakage-free comparison).

Team metrics are goals for and against, shots, shots on target, shots and shots on target conceded, fouls, corners, yellows, reds, total goals, clean sheet and both teams to score. Player metrics are goals, assists, shots, shots on target, tackles won, fouls drawn, fouls committed, yellows, reds, minutes and carded.

---

## Architecture

```
football-data.co.uk ─┐
FBref               ─┼──►  ingestion jobs  ──►  PostgreSQL  ──►  FastAPI  ──►  React
ESPN                ─┘       (scheduled)        (canonical)     (read-only)
```

One rule governs the whole design: **external sources are touched only by scheduled ingestion**. Nothing in the request path fetches, scrapes or calls out. Everything the interface shows is served from the local database.

Two further conventions follow from that:

- **Fact rows are per entity, per match.** `team_match` and `player_match` hold one row per team or player per fixture. Rolling windows are SQL window functions computed at query time; nothing rolling is stored, so a window can be re-cut by scope, venue, opponent or minutes floor without a rebuild.
- **Every row is scope-tagged.** Each fact row carries its competition and one of four competition types (`club_league`, `club_cup`, `club_european`, `international`). "Last five games" is meaningless without it, so a window is read within a scope by default and an all-competitions window is always an explicit, labelled choice.

Ingestion is idempotent throughout: re-running a job upserts and never duplicates, and the nightly pass fetches only fixtures not already held.

---

## Data sources

Sources are chosen per data type rather than per provider, so each has a single well-defined job. Every fact row records which source produced it, so "has this source published?" can be asked per source rather than inferred from a row's existence.

| Data | Source | Why |
|---|---|---|
| League team statistics (history) | football-data.co.uk | Static CSVs, no rate limit, deep history — but leagues only |
| League team statistics (recent) | ESPN | football-data.co.uk can lag days behind a match, so recent fixtures are to be written from ESPN and reclaimed once it publishes — decided and migrated, writer in progress ([ADR 0015](docs/adr/0015-current-team-data-from-espn.md)) |
| Cup, European and international team statistics | FBref | football-data.co.uk does not cover them |
| All player statistics | FBref | The only free source with per-match player event counts |
| Upcoming fixtures, squads, points deductions | ESPN | Display and membership only, never a statistics source |

Sources disagree, and sometimes one is simply wrong. Comparing football-data.co.uk against FBref and using ESPN as arbiter confirmed 27 erroneous values in the former and six in the latter — mostly transcription slips of ten, twice a whole fixture's figures swapped with another's. No source is assumed correct: a correction requires two independent sources agreeing against the third, and confirmed errors are overridden at ingest.

The sources also count differently. football-data.co.uk scores a two-booking dismissal as one red card and no yellows, where FBref counts both bookings. Left unreconciled that changes what a metric means partway through a rolling window, so any source writing team rows is reconciled to football-data.co.uk's convention first.

Names differ across providers too ("Man Utd" against "Manchester United"). Source names are resolved to a canonical identifier during ingestion via per-source ID columns; nothing is ever joined on a display string. Unrecognised names fail the job loudly rather than quietly creating a duplicate club.

---

## Operational design

Four scheduled jobs run daily, in an order that matters:

| Time | Job | Tier |
|---|---|---|
| 07:00 | Current-season league team data and points deductions | Unattended |
| 07:30 | Upcoming fixtures and finished-match detection | Unattended |
| 08:00 | FBref player data for any competition with pending work | Supervised |
| 09:00 | Squad membership refresh | Unattended |

The FBref pass is supervised because the source is rate-limited and bot-protected; the others are safe to run headless. The squad refresh derives its club list from the fixture slate, so it must follow the fixture job — a promoted club only joins the list once the new season's fixtures are laid down.

A recurring theme in the design is that **a source outage should be visible, not absorbed**. A coverage audit asks, per fixture and per source, whether the data that source owes has arrived. What each source owes is read from the same constants the ingestion jobs use, so the audit cannot drift from reality, and out-of-scope absences (League One player data, for instance) are correctly not reported as gaps. Anything recently overdue raises an alarm; older gaps become a standing count that stays visible rather than decaying into an assumption.

This matters because the failure that prompted it was silent. Treating "we have the results" as equivalent to "the match was played" meant an unpublished CSV read as *the match never happened*, which quietly withdrew those fixtures from the player pipeline as well. The job reported no pending work and exited cleanly. Seventeen fixtures were lost before the gap was spotted; [ADR 0014](docs/adr/0014-fixture-status-means-played-not-published.md) records the separation of the two ideas.

---

## Design decisions

Decisions are recorded as ADRs in [`docs/adr/`](docs/adr/). The ones that shaped the project most:

- [0001](docs/adr/0001-data-sources-after-fbref-opta-loss.md) — rebuilding the sourcing strategy after FBref lost its Opta feed
- [0004](docs/adr/0004-promotion-playoffs-as-separate-competition.md) — modelling the promotion play-offs as their own competition, to stop them contaminating league form
- [0007](docs/adr/0007-team-identity-via-fbref-id.md) — resolving team identity by source ID rather than name
- [0011](docs/adr/0011-european-and-international-scope.md) — deciding international coverage by competition rather than by nation
- [0013](docs/adr/0013-squad-membership-from-espn-roster.md) — sourcing squad membership from a roster rather than inferring it from appearances
- [0014](docs/adr/0014-fixture-status-means-played-not-published.md) — separating "was played" from "has been published"
- [0015](docs/adr/0015-current-team-data-from-espn.md) — splitting league team data by recency rather than by provider

[`CONTEXT.md`](CONTEXT.md) is the domain glossary and the best single starting point for the vocabulary the code uses: Fixture, Team-Match, Metric, Summary Metric, Rolling Window, Threshold, Spell, Covered tie, Overdue.

---

## Running it locally

Developed against Python 3.13, Node 24 and a local PostgreSQL instance. The commands below are Windows paths; substitute `.venv/bin/python` elsewhere.

**Backend**

```bash
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

Copy `backend/.env.example` to `backend/.env` and set `DATABASE_URL`, then apply the migrations:

```bash
.venv/Scripts/python.exe -m alembic upgrade head
```

Seed the reference data and backfill team statistics:

```bash
.venv/Scripts/python.exe -m ingestion.config_sync
.venv/Scripts/python.exe -m ingestion.seed_competitions
.venv/Scripts/python.exe -m ingestion.teams
.venv/Scripts/python.exe -m ingestion.team_match
```

Start the API, which serves interactive documentation at `/docs`:

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

**Frontend**

```bash
cd frontend
npm install
npm run dev -- --port 5173
```

**Tests**

```bash
cd backend
.venv/Scripts/python.exe -m pytest
```

Player ingestion is a separate, supervised step against FBref; see the commands section of [`CLAUDE.md`](CLAUDE.md) for the full runbook.

---

## Repository layout

```
backend/
  app/           read-only FastAPI: endpoints, rolling-window queries, schemas
  ingestion/     one module per source and per job; the only code that fetches
  alembic/       schema migrations
  tests/         pytest suite
frontend/
  src/           React + TypeScript + Tailwind interface
docs/
  adr/           architecture decision records
CONTEXT.md       domain glossary
CLAUDE.md        working constraints and the operational runbook
```

---

## Status

In use and ingesting daily. Player coverage for League One and League Two is the main outstanding expansion, deliberately deferred until the Premier League and Championship pipelines have proved themselves across a full season.

Personal, single-user, and run locally by design: there is no authentication, no multi-tenancy and no public deployment. Data is used under the personal-use terms of the free sources; nothing is redistributed.
