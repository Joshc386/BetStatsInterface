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

## Update — international finals + Nations League landed (2026-07-08)

The seven league-format/tournament competitions backfilled in full in one chain
day (+ a cached recovery pass): **1,052 fixtures = every match of every covered
edition 2020-21 → 2025-26, 32,138 player rows, 2,086 team rows (100% corners on
played matches)** — World Cup 2022, Euros 2020/2024, Copa América 2021/2024,
AFCON 2021/2023/2025, Asian Cup 2023, Gold Cup 2021/2023/2025, Nations League
×3. The stored season derives from each match date (August boundary), so summer
tournaments sit whole in the season just ended and straddling NL editions split
across two stored seasons by design. International match pages use FBref's
condensed format (`summary` carries all our metrics inline), so the cup ingest
path worked unchanged. Eight nations needed two-spelling aliases (schedule
SHORT vs player-df FULL: N. Macedonia, Equ. Guinea, UAE, Dominican Rep.,
Trin & Tobago, St. Kitts & Nevis, Rep. of Ireland, Bosnia–Herz). **Nine honest
gaps, all real-world anomalies:** two COVID-cancelled awarded games (Nov 2020),
six unplayed fixtures from Russia's 2022 suspension (empty fixtures, no rows),
and the abandoned Romania–Kosovo (Nov 2024; player rows, no team rows — no
final scorebox).

**The qualifiers remain deferred**: FBref renders qualifier *history* pages
without the `table#seasons` element soccerdata's `read_seasons` requires, so
every qualifying competition (WC quals ×7 confederations + play-offs, Euro/
AFCON/Asian qualifying) crashes upstream at season resolution. The season links
are present in the page, so a small vendored `read_seasons` shim is feasible —
proof-of-concept agreed as the follow-up (2026-07-07) before the qualifier
backfill (the larger half of the ~3,500–4,500 estimate) is attempted.

## Update — qualifier scope grilled, shim proven (2026-07-09)

The `read_seasons` shim PoC passed both halves. Offline: a ~40-line `sd.FBref`
subclass (`ingestion/fbref_shim.py`) falls back to mining the per-edition `h2`
headings when `table#seasons` is absent — validated against all 10 cached
qualifier history pages (20 real editions resolved; the in-progress edition's
unqualified URL is handled by the same headings). Live: WC Qual UEFA 2022
schedule parsed **259/259 rows with game_ids**, rounds intact, and a match
probe (Belgium–Wales) carried the full condensed stat set — the existing
`ingest_match` path works unchanged. Note soccerdata's season-code quirk:
single-year `parse("2021") → "2020"`, symmetric on both sides, so Euro-2020
qualifying answers to fetch code "2020" *and* "2021".

Scope decisions from the grill:

- **Editions: 21** — WC qual 2022+2026 (6 confederations + inter-confed
  play-offs), Euros Qualifying "2021"+2024, AFCON Qualifying
  2021/2023/2025/2027, Asian Cup Qualifying 2023. Plus the **in-progress WC
  2026 finals** (shim-free), ingested first and re-run after the July 19 final.
- **Date cutoff at ingest: skip matches before 2020-08-01.** Makes the "dates
  rule" mechanical and saves ~400+ out-of-window match-page fetches (Euro qual
  "2021" alone is ~250 matches of 2019 with only the Oct/Nov 2020 play-offs in
  window).
- **One "World Cup Qualifiers" competition** for all confederations + the
  play-offs (region self-evident from the nations; `stage` carries the round);
  Euros/AFCON/Asian Cup qualifying stay 1:1 as their own competitions. Four
  new Competition rows, not eleven.
- **Dual-badged AFC matches → the World Cup label wins** (see CONTEXT.md,
  **Dual-badged match**): chain order runs WC Qualifiers before Asian Cup
  Qualifying; dedup makes the second pass skip the shared match ids, so Asian
  Cup Qualifying's audit count is expectedly below its schedule count.
- **Honest upstream gap:** FBref has no 2027 Asian Cup qualifying page at all,
  so its third round (Mar 2025 – Mar 2026, fully in-window) cannot be ingested.
  Documented, not worked around.
- **Upcoming slate joins the scope:** add the World Cup to
  `upcoming.ESPN_LEAGUES` with first-contact `espn_id` stamping onto the
  nation rows the FBref ingest created. Acceptance test is live now: the 2026
  semi-finals must materialise on the daily run once the quarter-finals
  resolve them (a fixture cannot exist with an undecided side by design).

## Update — qualifier backfill landed (2026-07-21)

All 21 qualifier editions ingested in three chain sessions + recovery passes
(2026-07-20/21), plus the WC 2026 finals swept to completion after the July 19
final. **Final state:**

| Competition | Fixtures | Team rows (corners) | Player rows | Date range |
|---|---|---|---|---|
| World Cup | 168 | 336 (100%) | 5,283 | 2022-11-20 → 2026-07-19 |
| World Cup Qualifiers | 1,658 | 3,250 (46%) | 50,357 | 2020-10-08 → 2026-03-31 |
| Euros Qualifying | 251 | 502 (100%) | 7,807 | 2020-10-08 → 2024-03-26 |
| AFCON Qualifying | 415 | 744 (**0%**) | 11,333 | 2020-11-11 → 2026-03-31 |
| Asian Cup Qualifying | 40 | 80 (0%) | 1,244 | 2021-10-07 → 2022-06-14 |
| **Total** | **2,532** | **4,912** | **76,024** | |

Zero unrecovered alias/guard trips at close — every skip during the live runs
was resolved by the recovery-pass pattern (add alias/allowlist entry, re-run,
cache absorbs it at ~zero network).

**11 nation aliases + 6 allowlist entries** added across the three chains (on
top of the 8 from finals+NL): Antigua–Barbuda, St. Vincent, British V.I.,
Turks & Caicos, Papua NG, CAR, São Tomé, Brunei (aliases); New Caledonia,
Congo, South Sudan, Korea DPR (guard-token allowlist — each a real nation
colliding with an existing team's first-word guard token, never a duplicate).

**Confirmed honest gaps, not bugs:**
- **AFCON Qualifying + Asian Cup Qualifying carry ZERO corners** — verified
  against a cached match page: the `team_stats_extra` panel is entirely absent
  from these competitions' pages (same shape as the 34 FA Cup third-round
  pages in ADR 0008). Not a parser miss; the panel doesn't exist upstream for
  these confederations.
- **World Cup Qualifiers sits at 46% corners** (vs ~100% for UEFA-only
  competitions) — the figure blends corners-complete confederations (UEFA,
  CONMEBOL) with corners-thin ones (CAF, CONCACAF, OFC minnows), consistent
  with the ADR's accepted minor-confederation metric sparsity.
- **69 no-player-row fixtures** (28 WCQ + 41 AFCON Qualifying): a recognizable
  cluster of chronic African-qualifier withdrawals (Chad, Eritrea, Somalia,
  South Sudan, Burundi, Djibouti each account for 4-6) plus small-nation
  forfeits (Caribbean/Pacific minnows) and the **Russia suspension** carrying
  into qualifiers (Russia v Poland, 2022-03-24 walkover) — the same shape as
  the finals+NL backfill's honest gaps, at qualifier scale.
- **7 fixtures with player rows but no team rows** (abandoned matches, no
  final scorebox): includes the well-known **Brazil v Argentina (2021-09-05)**
  — health officials halted the match ~7 minutes in over a COVID-quarantine
  dispute; it never resumed. Same shape as finals+NL's Romania–Kosovo.

The qualifier `read_seasons` shim (`ingestion/fbref_shim.py`) needed no
changes once proven; the one code fix mid-backfill was a dateless-schedule-row
guard (OFC 2022's cancelled Tonga–Cook Islands fixture had a `game_id` but no
date, crashing `season_for_date`). ADR 0011's original scope for internationals
is now fully delivered: finals, Nations League, and every qualifying campaign,
six seasons deep.

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
