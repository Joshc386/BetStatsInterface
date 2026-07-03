# Upcoming fixtures via the ESPN scoreboard API

**Status:** accepted — supersedes the "upcoming scheduled fixtures → football-data.co.uk
`fixtures.csv`" half of ADR 0003 (the finished-match `game_id` half stands unchanged).

The fixture view needs *upcoming* (`status='scheduled'`) fixtures for its landing-page
slate. ADR 0003 routed these through fd.co.uk's `fixtures.csv`, but planning the build
surfaced two disqualifiers the user called out: English fixture lists are **volatile**
(kick-off dates/times reshuffle continuously for TV picks, European commitments and cup
replays — the June release is an ordering, not a timetable) and **cup fixtures don't
exist until each round is drawn**. `fixtures.csv` is a ~one-week rolling league-only
feed (verified live: 12 stale Belgian/Spanish rows in the off-season), so it can never
carry cups and gives almost no forward horizon. A useful upcoming feed must be
**re-ingested at intervals from a source that tracks changes**, or it is not worth
building.

## Decision

**Source upcoming fixtures from ESPN's public scoreboard JSON**
(`site.api.espn.com/.../soccer/<league>/scoreboard?dates=<range>`) — the spec already
sanctions ESPN as the schedule fallback. Verified live (2026-07-03): eng.1 / eng.2 /
eng.4 all serve scheduled 2026-27 fixtures with kick-off datetimes, venues, event
status, and stable numeric team ids; `eng.fa` / `eng.league_cup` are the same shape, so
cup ties flow in automatically once drawn (covered-tie filter applies, per ADR 0008).

- **Refresh model: rolling re-ingest, upsert on the fixture natural key.** A run
  fetches a forward window (default 45 days) per configured league — a handful of
  unauthenticated requests, no key, no rate-limit drama. Date/time changes simply
  upsert; a fixture is **never demoted** from `finished` back to `scheduled`. Only
  `STATUS_SCHEDULED` events are ingested; a postponed game vanishes from the feed, its
  stale row ages out of the (future-dated) upcoming query, and the rescheduled date
  upserts back in. Manual CLI now; becomes nightly Job B work in Phase 5.
- **Reconciliation: `teams.espn_id`, backfilled deterministically on first ingest.**
  ESPN uses long-form names ("Wolverhampton Wanderers" vs canonical "Wolves"), so the
  first run matches by normalised name + a deterministic `ESPN_TEAM_ALIASES` map
  (fail-loud on anything unresolved — the FBref alias pattern, ADR 0007), then stamps
  the ESPN numeric id so every later run resolves by id, never by name.
- **No schema change.** `teams.espn_id` and `fixtures.status`/`ix_fixtures_status_date`
  already exist; the fixture natural key makes an ESPN event-id column unnecessary.

## Considered options

- **fd.co.uk `fixtures.csv`** (ADR 0003's choice) — rejected: ~one-week horizon, no
  cups, and in the off-season it is empty for England precisely when season prep
  happens. The odds-heavy columns are also dead weight for a project that excludes odds.
- **FBref `read_schedule` as the upcoming feed** — re-rejected (as in ADR 0003): drags
  Cloudflare + the FBref rate limiter into a refresh loop for no gain.
- **football-data.org API** — free tier covers PL/Championship but not League One/Two
  or the FA Cup, and needs a key + request budget. Coverage fails the four-tier scope.
- **api-football (RapidAPI)** — full coverage but keyed, quota'd, and a third-party
  dependency for data ESPN gives freely.

## Consequences

- A fourth source enters the reconciliation seam (fbref_id / fdcouk_name / espn_id);
  the dormant `espn_id` column becomes load-bearing. Unknown-name failures at ingest
  are alias work, same muscle as FBref.
- The upcoming feed is **display-only input** — scheduled fixtures carry no stats and
  are never a stats source; results still arrive exclusively via fd.co.uk/FBref paths,
  which overwrite the scheduled row through the same natural key.
- ESPN is unofficial and could change shape; the ingest is fail-loud and the surface
  degrades to "no upcoming fixtures" rather than wrong data.
- Cup upcoming fixtures appear automatically per round once draws are made, filtered to
  covered ties — no new build needed when that happens.
