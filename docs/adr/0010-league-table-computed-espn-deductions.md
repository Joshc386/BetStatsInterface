# League table: computed from team_match, deductions seeded from ESPN standings

**Status:** accepted

The UI needs a league table per league-season. Two sourcing options: fetch
standings from a provider, or compute them from the `team_match` rows we
already hold for all four tiers across six seasons.

## Decision

**Compute the table** (`app/table.py`, `GET /table`) — one SQL aggregation over
`team_match` (`W/D/L` from the generated `result` column, GF/GA sums), sorted
by the English league tie-break (points, goal difference, goals for, name).

Why compute rather than ingest a standings feed:

- **Rule 1 already forbids request-time fetching**, so a "live" table would
  really be another scheduled ingestion job + table + reconciliation — more
  pipeline than a query, for less capability.
- **Self-consistency**: a computed table always agrees with every other number
  in the app; a fetched one can silently disagree with our own results.
- **As-of-date tables** (`?as_of=`) fall out of the same query — "were they in
  a relegation scrap when these two met in February?" — which no standings
  feed provides historically.

**The one input results can't derive is administrative points changes** (PSR /
insolvency deductions — common enough: 14 club-seasons in our 6-season window,
including three Championship rulings in 2025-26 alone). These live in
`points_adjustments` (one row per club-season: points, note; negative =
deduction), applied inside the computed total and footnoted in the UI.

**Deductions are seeded from ESPN's standings JSON**
(`site.api.espn.com/apis/v2/sports/soccer/<league>/standings?season=<year>`),
extending ADR 0009's ESPN scope from fixtures to standings-deductions —
ingestion-layer only, via `ingestion/points_adjustments.py` (dry-run default,
`--apply` to upsert; espn_id-first team resolution per ADR 0007/0009).
Verified: the feed carries a populated `deductions` stat (value + ruling note)
historically for all four tiers (Everton 8.0 / Forest 4.0 in eng.1 2023;
Wigan 8 / Reading 6 in eng.3 2023). Re-run in-season for new rulings; wire
into the Phase 5 nightly when it lands.

## Consequences

- Deduction *dates* are not stored (ESPN doesn't provide them); `as_of` views
  apply the season's full adjustment. Acceptable: a mid-season historical
  table is a research context view, not a point-in-time legal record.
- Known-output regression tests pin the computation to published finals
  (PL 2324: Everton 40 after −8, Forest 32 after −4; Derby 34 after −21 in
  2122) plus cross-season invariants (`tests/test_table.py`).
- A new deduction appears in the table only after a re-run of the seed job —
  the table is as current as the last ingest, like everything else.
