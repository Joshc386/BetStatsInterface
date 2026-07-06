# European and international scope (scoped ahead of ingestion)

**Status:** accepted (scope settled 2026-07-05; ingestion pending)

The last two Competition Types with no data — `club_european` and `international` —
scoped in full via a grill session so neither needs re-litigating when its build slot
arrives. Both reuse ADR 0008's cup pattern (FBref `LEAGUE_DICT` entry → schedule →
covered-fixture filter → get-or-create Fixture → `ingest_match`; team rows derived
zero-network from the cached pages afterwards). The schema needs nothing: both enum
values have existed since migration 0001. **Priority (re-set in the same session):
Europe first, then internationals — League One/Two player expansion is set aside for
now and re-scoped when picked back up.**

## Decision — `club_european`

- **Covered ties only, rule extended verbatim from ADR 0008:** a European fixture is in
  scope when at least one side has PL/Championship league rows that season. The foreign
  opponent is stored in full *for that fixture* (canonical team row by `fbref_id` with
  **country recorded**, team + player rows) but its other fixtures are not held — when
  other domestic leagues are covered later (a stated ambition), identities and partial
  histories are already seated and the filter just loosens.
- **Four competitions, one type:** Champions League, Europa League, Conference League,
  UEFA Super Cup — each its own `competition_id` under `club_european` (the ADR 0004/0008
  no-new-enum pattern). **The FIFA Club World Cup is excluded permanently**: squads
  demonstrably don't take it seriously and opposition is far weaker, so its rows would be
  form noise inside a covered club's Rolling Windows. Signal quality, not fetch cost.
- **Six-season depth (2020-21 → 2025-26)** matching every other scope — a shallower
  horizon would make cross-scope season windows quietly incomplete. ~450 covered
  fixtures; one or two user-run evenings.

## Decision — `international`

- **Purpose is player-data continuity, not fixture research:** a tracked player's
  competitive caps exist in his Appearance history, so an international break is not a
  hole in his form arc. Scope-purity is unchanged — caps never enter a league window by
  default; the all-competitions window is an explicit, labelled user choice (CONTEXT.md,
  **Competition Type**).
- **Coverage is by competition, not by nation.** The list: World Cup, Euros, Copa
  América, AFCON, Asian Cup, Gold Cup, their qualifying campaigns (all confederations
  for the World Cup), and the UEFA Nations League. Every match in a covered competition
  is ingested wholesale. **Friendlies are excluded permanently** (the Club World Cup
  reasoning). Continental Nations-League equivalents (CONCACAF/CAF) are out as marginal.
- **Metric sparsity is accepted, never patched:** minor-confederation pages may carry
  only lineups/goals/cards — those rows still deliver appearance/minutes continuity
  (itself the fatigue/form signal) and their missing Metrics stay NULL, surfaced
  honestly. A per-competition sourcing spike precedes the build (the ADR 0008 pattern)
  so thin confederations are known going in, not discovered mid-backfill.
- **Six-season horizon applies to match dates, not campaign completeness** — a
  qualifying campaign straddling the boundary is held partially. Form windows care about
  recency; campaign completeness is a non-goal. ~3,500–4,500 matches (~20–30h of
  user-run chains) — the largest single backfill on the books, which is why it follows
  Europe, not precedes it.

## Update — European ingestion landed (2026-07-06)

The three league-format competitions backfilled in full on the first live day:
**502 fixtures (every covered tie, 2020-21 → 2025-26), 15,292 player rows,
1,002 team rows (94% with corners)**. Two failure shapes surfaced and were
fixed in-flight (commit `533350f` + follow-up): shared acronyms ("AEK") joined
the guard's generic tokens, and ten foreign clubs needed two-spelling aliases
(schedule SHORT vs player-df FULL). **The UEFA Super Cup is deferred**: its
single-match FBref schedule page has no date column and soccerdata's
`read_schedule` crashes on it upstream — patching a vendored parser for the 3
covered matches (Spurs 2526, City 2324, Chelsea 2122) fails the boring-path
test. Revisit only if those matches are ever missed; they can be hand-seeded.

## Considered options

- **Whole European competitions (PSG vs Bayern in scope)** — rejected: a different
  product. Foreign clubs' league form would not be held, so their form windows would be
  misleading; the alias/reconciliation surface explodes for fixtures the user does not
  research ("would you ever research a fixture with no English side?" — no).
- **England-only internationals** — rejected as *dishonest*: with most PL players
  capping for other nations, a side-by-side "form including internationals" would
  silently include one player's break and not another's — partial coverage that corrupts
  the exact number being trusted.
- **"Covered nation" filter** (nations fielding tracked players) — rejected: English
  squads span most footballing nations, so the filter saves only ~30–40% of pages while
  adding a boundary that drifts with every transfer window, plus two standing
  obligations (new-signing backfills; roster-dependent break ingestion) that
  whole-competition coverage simply doesn't have.

## Consequences

- The scope selector (shipped 2026-07-05) generalises as-is: `club_european` becomes a
  form-window option when data lands. Whole-match (ET-inclusive) Metric caveats apply as
  for cups.
- Orphan rows accumulate by design (a Tajikistan qualifier's players, like a non-league
  FA Cup opponent's today) — harmless bonus data toward future coverage.
- Build-time flags, deliberately not scoped here: `LEAGUE_DICT` entries per competition
  (10-minute spike each); a season-tagging convention for internationals (a June World
  Cup sits between club seasons); ESPN `espn_id` reconciliation before European/
  international fixtures join the upcoming slate; the first-word alias guard's
  ergonomics at foreign-name volume (it will trip often on "Real …"/"FC …"/"AC …").
- Phase 5's nightly job gains in-season European match days (from Sept 2026) and, once
  internationals land, break-week fetches over the competition list — both the same
  incremental shape as league/cups.
