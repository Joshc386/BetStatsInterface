# Domestic-cup player ingestion (FA Cup, EFL Cup)

**Status:** accepted

The first expansion beyond `club_league`: adding **FA Cup** and **EFL Cup** data so the
Cups scope (already present in the UI, and exercised only by the Championship play-offs
to date) carries real domestic-cup form. A sourcing spike confirmed soccerdata's FBref
reader is config-driven (any competition in FBref's `/comps/` index works via a
`LEAGUE_DICT` entry — FA Cup is comp 514, EFL Cup 690) and that the player-ingest path is
competition-agnostic (match player-stat pages are identical across competitions).

## Decision

**Ingest cup data player-only first, scoped to ties involving a tracked club, via a
dedicated entrypoint that creates fixtures from the FBref cup schedule.**

- **Player data only in this pass; team data layered later.** `team_match` is
  football-data.co.uk-only and that source does not cover cups; an FBref→`team_match`
  path does not exist. Player-only reuses the ready, competition-agnostic player pipeline
  and ships the higher-value half (rotation/cup-prop research). Cup *team* rows
  (from FBref scorelines/match team-stats) are a deferred follow-up — the same deferral
  ADR 0004 made for the play-offs. While team data is absent, Head-to-Head stays
  league-only as today. **(Follow-up now landed — see "Update" below.)**
- **Scope = ties with at least one PL or Championship club, season-aware.** "Covered"
  is decided per season from `team_match` (the club has league rows in PL/Championship
  that season), so a club relegated out of the top two does not drag its cup ties in for
  an untracked season. This auto-excludes the lower-/non-league-only early rounds.
  `ingest_match` reads both squads, so the opponent's players (often League One/Two) come
  along as a bonus toward future coverage. The filter loosens naturally once League
  One/Two player data lands.
- **Dedicated cup backfill entrypoint, not an extension of `link_fixtures`.** League and
  play-off ingestion *match* pre-existing football-data.co.uk fixtures; cups have none,
  so the cup path **creates** the Fixture from the FBref cup schedule: `read_schedule`
  → filter to covered ties → get-or-create the cup Fixture → resolve both teams by
  `fbref_id` (auto-create + log unknown opponents, per ADR 0007) → `ingest_match`
  (reused unchanged). Add `ENG-FA Cup`/`ENG-EFL Cup` to `soccerdata_config/league_dict.json`
  and seed two `club_cup` competitions: **"FA Cup"** and **"EFL Cup"**
  (`country='England'`, `tier=None`, `fbref_key` set, `fdcouk_key=None`) — same shape as
  "Championship Play-offs".
- **Seasons: 2324, 2425, 2526** — aligned to existing PL/Championship player coverage.
  Greater depth is a later, deliberate pass.
- **All domestic knockouts stay `club_cup`** (FA Cup, EFL Cup, play-offs); they remain
  distinguishable by `competition_id`, so the existing competition filter / segment-by
  isolates "FA Cup form" vs "EFL Cup form" without a new enum value (as ADR 0004 chose).

## Considered options

- **Team + player in one pass** — rejected for now; it requires building the absent
  FBref→`team_match` path. Layered (player → team) ships value sooner and keeps the
  passes reviewable.
- **Ingest the whole cup** (all rounds) — rejected; it creates masses of non-league
  teams and untracked player rows and triggers fail-loud reconciliation on every unknown
  name, for data outside scope.
- **Fail-loud on unknown opponents** (the league pattern) — rejected for cups; a random
  draw cannot be pre-aliased, and stopping would silently drop a tracked club's real cup
  tie (including the giant-killings most worth seeing). Auto-create + log instead
  (ADR 0007).

## Consequences

- Cup player rows appear under the Cups scope immediately (UI already supports it);
  segment-by-competition separates the three knockouts.
- Depends on ADR 0007: cup opponents are resolved/auto-created by `fbref_id`, so the
  expansion does not breed duplicate teams.
- A cup-specific idempotency/regression guard asserts one Fixture ↔ one `fbref_match_id`
  — the natural key `(competition_id, season, home, away)` is collision-safe for cups
  (replays and two-legged semis swap venue → distinct orientation; FA/EFL/league
  meetings of the same clubs differ by `competition_id`), but the play-offs proved this
  is exactly where contamination sneaks in, so it is guarded explicitly.
- The pattern generalises to the other domestic knockouts and, with the `LEAGUE_DICT`
  precedent, to European/international competitions later.

## Update — cup team data landed (2026-07-02)

The deferred `team_match` follow-up is now built (`cups.backfill_cup_team_match`,
`cups team <season> <cup>` CLI) for all six cup-seasons (804 rows = 2 × 402 fixtures).
**Zero-network** — it reuses data already on hand:

- `gf`/`ga` from the cached match **scorebox** (`players.parse_scoreline`) — authoritative
  where a summed player-goal count is not (FBref credits no player for an opponent
  own-goal). A 10+ score carries class `"score double"`, matched via the `score` class
  token (regression: Man City 10-1 Exeter was first dropped by an exact `class="score"`).
- `shots`/`sot`/`fouls`/`yellows`/`reds` by summing the fixture's `player_match` rows;
  `shots_conceded`/`sot_conceded` from the opposite side.
- **Coverage caveats (honestly surfaced, not hidden):** `xg` stays NULL as everywhere.
  `shots`/`sot`/`fouls` are NULL for the ~8% of cup matches where FBref's source itself
  is sparse (goals + cards are always present). `corners` was initially left NULL on the
  belief that FBref cup pages omit the team-stats-extra panel — **that belief was wrong;
  see the corners update below.**

Because cup `team_match` is *derived* from the same FBref data as the player rows, its
shot/foul totals cannot cross-validate that player data (equal by construction) — but the
scorebox `gf`/`ga` is independent, so the standing `sum(player goals) ≤ gf` invariant now
meaningfully spans cup rows. Head-to-Head is no longer league-only for covered cup clubs.

## Update — cup corners + play-off team rows (2026-07-03)

The corners gap is closed, **zero-network, from the same cached pages**. An offline scan
falsified the premise above: **368 of 402 cached cup match pages carry the
`team_stats_extra` panel with parseable Corners values.** The 34 without it are all FA
Cup third-round ties (30 of them the 2024-25 R3 weekend; pages fetched mid-2026, long
after the matches, so the panel is genuinely absent upstream, not stale-cache).

- **Source: `parse_corners` over the cached page's `team_stats_extra` panel**, wired into
  `backfill_cup_team_match` (`corners` joined `_CUP_TEAM_METRICS`). The 34 panel-absent
  fixtures stay NULL — honest sparsity, same as shots/fouls. **FotMob was rejected**: the
  earlier "likely FotMob later" note predated ADR 0002's finding that soccerdata 1.9.0
  removed the FotMob reader; a hand-rolled unofficial-API client for 34 fixtures fails
  the boring-path test. Understat (the surviving xG path) carries no corners.
- **Whole-match figures — extra time included.** FBref publishes no 90-minute split, and
  every other cup metric (scorebox `gf`, player-row sums over 120') already includes ET.
  Recorded in CONTEXT.md under **Metric**: a Cups-scope window mixes game lengths, and
  90-minute-quoted thresholds (corner lines) should be read accordingly.
- **Parser validated cross-source before trusting cup values:** parsed FBref corners
  diffed against the *independent* fd.co.uk corners on all 2,795 comparable league
  fixtures with cached pages → **95.7 % exact agreement**; 112 of the 120 diffs are
  one-side ±1 (routine inter-provider counting variance); the 8 structural diffs were
  audited individually — every fixture link verified (page team ids == fixture teams),
  every FBref page internally coherent (corners track crosses/fouls), and the largest
  (Arsenal 13–3 Burnley, PL 2324) confirmed by ESPN. Where they disagree structurally,
  **fd.co.uk is usually — not always — the wrong side** (incl. one home/away swap and
  one crossed final-day pair in its own data). The five cases a third source (ESPN/Opta)
  confirmed against fd.co.uk are now overridden at ingest via
  `team_match.CSV_CORRECTIONS` (fail-loud, applied to the raw CSV row so re-running the
  backfill can't reinstate them). Blackburn–Birmingham 2324 proved FBref can be the
  wrong side too (ESPN sides with fd.co.uk), so **only third-source-confirmed
  corrections are registered**; unverifiable or ±1-magnitude diffs stay as ingested.
- **Championship Play-offs team rows built** by the same run: the play-offs were the one
  remaining `club_cup` competition with no `team_match` rows (the ADR 0004 deferral).
  All 15 fixtures had cached pages with the panel → 30 rows, corners included; the CLI's
  `LEAGUE_IDS` gate now applies to player (live-fetch) mode only.

End state: **834 club_cup team rows, 766 with corners**; the 68 NULLs are exactly the
34 FA Cup R3 fixtures × 2. Unlike shots/fouls, corners is NOT derivable from player
rows, so FBref-vs-fd.co.uk corner agreement is a genuine cross-source check — the
validation above doubles as evidence for the whole cup team-data path.
