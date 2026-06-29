# Squad form from appearances (no roster source in v1)

**Status:** accepted — revisits ADR 0003 (which had leaned toward a FBref-squad-page roster job)

The Squad-form panel needs to answer "who is in this club's squad, and how is each
member performing?" for the **Fixture view** (both teams side by side) and the
**Team hub** (one team). ADR 0003 planned to source *membership* from a weekly FBref
squad-page roster job (Job C) into the `squads` table, keeping appearance history only
for the form numbers. That roster job was never built, and on revisiting it for this
deliverable we chose not to build it for v1.

## Decision

**Derive both squad membership and form entirely from FBref appearances
(`player_match`). No roster source — no FBref squad page, no Transfermarkt, no ESPN —
in v1.** This introduces a new glossary term, **Recent squad** (see `CONTEXT.md`),
distinct from the registered-roster **Squad** (whose source is now deferred past v1).

- **Membership = appearance-derived, at query time.** A player is in club C's Recent
  squad iff the `team_id` of his most-recent appearance *(any competition)* is C —
  `DISTINCT ON (player_id) … ORDER BY date DESC`, kept where `team_id = C`. Membership
  is scope-*independent* (a player whose last game was a cup tie still belongs).
- **The stored `current_team_id` is NOT trusted.** `ingestion/players.py` `_upsert_player`
  sets it to the *last-ingested* match's club (`on_conflict_do_update`, backfill order),
  which is **not guaranteed chronologically latest**. Current club is therefore derived
  from the `MAX(date)` appearance at query time, never read from `current_team_id`.
- **The per-player figure is his Summary Metric at this club** — his last-N appearances
  filtered to `team_id = C` (the team_id filter added in commit `564123a`), within the
  selected scope, rendered per-appearance or, with a Threshold, as a hit-rate. Not his
  form at a previous club.
- **Compute mirrors ADR 0005: raw rows + client-side aggregation.** A per-team endpoint
  `GET /teams/{id}/squad-form?scope=…` returns `{members, rows}` — membership resolved
  server-side, plus each member's last ~30 `player_match` rows at C (all scopes, every
  metric column). The client groups by player, filters scope, takes last N, and computes
  the figure with the existing `aggregate.ts` `summarise()` helper (the ADR 0005 oracle-
  checked function). Metric, N, threshold, and scope all recompute client-side; only a
  team change refetches. One per-team endpoint serves both surfaces (fixture page calls
  it twice in parallel; team hub once) — there is no cross-squad computation, so no
  compare endpoint is needed.

## Considered options

- **FBref squad-page roster job (Job C → `squads` table)** — ADR 0003's original lean;
  now **deferred, not adopted**. It is the *correct* membership source (it includes an
  unplayed new signing and excludes a player loaned out), but it is a new rate-limited,
  Cloudflare-aware scheduled job plus markup to maintain, and it buys accuracy only for
  the early-season transfer-window weeks (see Consequences). Remains the documented path
  if the staleness ever bites in practice. The `squads` table and the `Squad` glossary
  term are kept for that future.
- **Transfermarkt for precise loan/contract status** — rejected again (a fourth source
  needing its own id reconciliation), consistent with ADR 0003.
- **ESPN current-roster source, with a "switch to appearance-derived after N games"
  hybrid** — considered this session, rejected for v1. It reintroduces exactly the
  roster-source cost appearance-derivation avoids (a fourth `espn_id` reconciliation, a
  new ingestion job per non-negotiable #1) plus a stateful mode-switch rule, to fix the
  same narrow early-season window. Could be the deferred roster source instead of FBref
  if a spike ever shows clean, matchable roster data for the lower tiers.
- **Trusting `current_team_id` for membership** — rejected: it is last-written, not
  chronological (above).

## Consequences

- **Accurate in-season, stale across the summer.** "Recent squad" is "the squad as it
  last took the field." During a season this is correct (who last played *is* who is
  available). Across the transfer window it lags: a **sold** player lingers in his old
  club's panel until he debuts elsewhere *in covered data* (the accepted **"ghost"** —
  the user will handle these manually), and a **new signing** appears only once he
  debuts. This is unavoidable without the rejected roster source, and is acceptable
  because it bites hardest in June–July when there are no fixtures to bet on.
- **Honestly labelled.** The panel is always surfaced as appearance-based, never implied
  to be the registered roster. The `last-seen` date per player (his `MAX(date)`, free
  from the returned rows) is both the freshness signal and the ghost-detector.
- **Cheap to upgrade later.** When/if the roster source lands, only *membership* switches
  from Recent squad to Squad; the form numbers and the whole client/endpoint shape are
  unchanged.
- **Deep squad over a season.** "Most-recent appearance at C" can surface 30–40 names
  across a season (deep-squad + cameos), not a tidy 25. The list is sorted (by figure or
  last-seen) so the long tail falls to the bottom; no hard cap in v1.
- **Bounded payload.** ~30 rows × ~35 players × 2 teams per fixture load — small for a
  single-user local tool, the same trade-off ADR 0005 accepted.
