# Current league team data comes from ESPN; football-data.co.uk stays the historical source

**Status:** accepted — splits ADR 0001's `club_league` team-data rule by *recency* rather than
replacing it, and **reverses ADR 0014's "ESPN remains a slate source and never a stats
source"** for team-level Metrics only.

ADR 0001 assigned `club_league` team data to football-data.co.uk exclusively. That held until
football-data.co.uk **did not publish `E0.csv` for 2026-27** at all — a genuine 404 behind
Apache's `mod_speling` HTTP 300, diagnosed 2026-08-23. Six played Premier League Fixtures had
zero Team-Match rows and the league table was blank, with no date by which it would resolve.
Championship, League One and League Two were each a full round behind on the same day.

The deeper problem is not the outage but the **latency floor**. football-data.co.uk publishes
in batches, roughly a day behind. FBref — the obvious second source, and the one this ADR
originally chose — proved slower still in live observation: a 12:00 kickoff that finished
around 14:00 had no match report more than two hours later, suggesting an end-of-day cycle.
Neither can keep a league table current on the afternoon the games are played.

ESPN can. Its match summary carried complete stats for a fixture **within ten minutes of the
final whistle**, and it is already a Tier 1 unattended dependency of this project.

## Decision

**Recent league Team-Match rows come from ESPN; football-data.co.uk remains the historical
authority; all player data stays on FBref.** Source per data type *and* recency:

- **Historical `club_league` team data → football-data.co.uk.** Unchanged. Deep history, no
  rate limit, and the `CSV_CORRECTIONS` overrides already built against it. This ADR does not
  touch backfill.
- **Current / recently-played `club_league` team data → ESPN**, from the match summary
  endpoint (`/summary?event={id}`). It carries **every Metric `team_match` stores** —
  `totalShots`, `shotsOnTarget`, `foulsCommitted`, `wonCorners`, `yellowCards`, `redCards` —
  plus possession, passes, tackles and interceptions that we do not. **All four tiers**, with
  no dependency on any other pipeline having run first.
- **All player data → FBref.** Unchanged, and not a close call: ESPN's per-player vocabulary
  is 15 fields with **no `tackles` at all** (team-level only) and **no `minutes`** — derivable
  from substitution clocks in `plays`, but approximate around stoppage time and dismissals.
  **Appearance** is defined as minutes > 0 and `per_90` divides by `minutes_total`, so both
  are load-bearing. ESPN is richer for team data and poorer for player data; the split follows
  the data, not the provider.
- **Team-Match rows carry their source** (`fdcouk` | `espn` | `fbref`), NOT NULL. This is
  **load-bearing, not bookkeeping**: `coverage.find_gaps` tests only
  `EXISTS (team_match WHERE fixture_id = ...)`, so the constant named `TEAM_FDCOUK` would be
  silenced by any other writer and the outage absorbed — the precise failure ADR 0014 exists
  to prevent. Without the column the audit cannot express its own question.
- **The audit filters on `source = 'fdcouk'`** and keeps alarming while the CSV is absent. No
  third tier: `KNOWN_GAP_AFTER` already demotes a gap to a standing, never-alarming coverage
  figure after 14 days.
- **football-data.co.uk reclaims** when it publishes — it stays the authority for the
  historical record, and its existing unconditional upsert already overwrites. The ESPN writer
  must write **only where no row exists or `source = 'espn'`**, or the two would overwrite
  each other forever.
- **`upcoming` owns the ESPN team rows**, immediately after it marks a Fixture finished — it
  already holds the ESPN payload identifying exactly which fixtures just completed, so this
  adds one summary request per newly-finished Fixture and no new scheduled job. `nightly`
  continues to own football-data.co.uk rows and to audit them, preserving ADR 0014's "each job
  audits the source it owns".
- **The schedule gains post-fixture slots, additively.** `upcoming` repeats after each kickoff
  cluster (provisionally ~14:30 / 17:30 / 19:30 / 22:30 — 15:00 dominates with 194 Fixtures,
  then 19:00, 12:00, 16:00, 20:00), **and every existing run is kept unchanged as the
  backstop**. Because ESPN is Tier 1 JSON, these slots need no supervision, no VPN state, no
  headful browser and no watchdog — the reason the original FBref-based version of this ADR
  was uncomfortable. `nightly` stays daily: polling football-data.co.uk four times a day only
  re-discovers the same absent CSV. `matchday` keeps its single supervised slot, because the
  player pipeline it drives is still Tier 2 and still FBref.

## Evidence

**ESPN agrees with football-data.co.uk, and is right when they differ.** On a full Premier
League matchday (2026-05-24, 10 matches, 20 team-sides) eight matches agreed exactly. The two
that did not were *swapped with each other* in our stored data — and FBref, consulted as an
independent third source, matched ESPN on both.

Broadening that into a zero-network sweep of all **11,208** league team-sides holding both
football-data.co.uk rows and FBref player rows, 25 showed a combined shots+SoT+fouls mismatch
of 8 or more. Putting all 25 to ESPN as arbiter resolved **every one cleanly** — no three-way
disagreements:

| Verdict | Metric-values |
|---|---|
| football-data.co.uk wrong | **27** (19 team-sides) |
| FBref wrong | 6 (2 team-sides) |
| All three differ | 0 |

The dominant signature is a **±10 error on a single metric** (`5/15`, `8/18`, `19/9`, `13/3`,
`14/4`) — a leading digit dropped or added in transcription. Two fixtures are wrong on all
three metrics for both sides (Hull v Preston 2023-10-28; Sunderland/Tottenham 2026-05-24),
consistent with whole-fixture mix-ups rather than cell typos.

**FBref was wrong twice**, which is why the `CSV_CORRECTIONS` third-source rule stands: FBref
alone would have "corrected" football-data.co.uk *into* an error in both cases.

## Considered options

- **FBref as the fallback** (this ADR's first draft, superseded same day). Rejected on
  measurement: its publish latency is hours-to-end-of-day, it is Tier 2 supervised so league
  table freshness would depend on a headful VPN-off run, and it cannot reach League One or
  Two at all — no player pass there means no cached pages and nothing to derive from.
- **Fetch the league table from ESPN standings.** Rejected: breaks ADR 0010's computed-table
  invariant and fixes only the table, leaving the database with zero Premier League matches
  while team pages, form and Rolling Windows stay empty.
- **ESPN for player data too.** Rejected on the parity bar: no per-player tackles, and minutes
  only by derivation.
- **First-writer-wins.** Rejected: a season would split permanently by source mid-way and
  `CSV_CORRECTIONS` would never reach those rows.
- **Replace the existing runs with post-fixture slots.** Rejected: keeping them makes the new
  cadence purely additive, so worst case is today's behaviour.

## Consequences

- **The Premier League table works again**, and because the fix restores the underlying
  Team-Match rows it reaches every surface, not the table alone.
- **The league table stops depending on football-data.co.uk's publishing schedule.** It stays
  *computed* from `team_match` (ADR 0010 unchanged) — what changes is which source fills those
  rows first. A 15:00 Saturday kickoff finishes ~16:50, the ~17:30 ESPN slot writes its team
  rows, and the table is current **the same afternoon** rather than waiting a day for the CSV.
  Note this is specifically an argument for ESPN and **against** deriving the table from FBref:
  FBref is *slower* than football-data.co.uk is late (observed 2026-08-23: a 12:00 kickoff
  finished ~14:00 still had no match report two hours on), covers only two of the four tiers,
  and is Tier 2 supervised. FBref's role in the table is **accuracy, not timeliness** — it is
  one of the three voices in the cross-source consensus check, never the fast path.
- **No new supervision burden.** ESPN is plain JSON — the added slots cost one request per
  league plus one per newly-finished Fixture, with no Cloudflare, VPN or machine-awake
  requirement. Team-data freshness is now decoupled from the Tier 2 player pipeline entirely.
- **ESPN becomes load-bearing for five things** — fixture slate (0009), cup played-detection
  (0012), Squad membership (0013), league played-detection (0014) and now team Metrics. That
  is real concentration risk on one free API. The mitigation is that football-data.co.uk still
  writes the historical record, so an ESPN outage delays current rows rather than losing them.
- **football-data.co.uk reclaiming can overwrite a correct ESPN value with a known-bad one**,
  in the ~27 confirmed cases and any like them. `CSV_CORRECTIONS` remains the remedy, and the
  `source` column makes such rows findable for the first time.
- **ESPN is not a backfill source.** Four of the 25 audited fixtures returned zero-filled stat
  blocks for older matches. Fine for current fixtures, unusable for history — which is why
  football-data.co.uk keeps that role rather than merely being tolerated in it.
- **A Rolling Window may span sources.** Cross-source agreement is high but not total, and is
  now identifiable via `source` rather than invisible.

## Open before implementation

- ~~ESPN's card convention is unmeasured.~~ **RESOLVED 2026-08-23: ESPN follows
  football-data.co.uk's convention, so no normalisation is needed.** Tested on seven fixtures
  containing an FBref second yellow (`yellows=2, reds=1`): ESPN matched football-data.co.uk in
  six and FBref in none. This is a material simplification — the normalisation step the
  superseded FBref draft required simply does not exist for ESPN, and ESPN rows are directly
  comparable with the historical football-data.co.uk record inside a **Rolling Window**. The
  seventh (Bristol City 2026-08-15) had football-data.co.uk and FBref agreeing on 2 against
  ESPN's 1 — an ordinary single-value disagreement, not a convention difference.
- **Slot times are provisional**, placed a guessed ~2 hours after each kickoff cluster. FBref's
  post-whistle publish latency is being measured directly; ESPN's appears to be minutes.
  Retune from real numbers rather than defending the guess.
- **The error sweep is partial.** It can only see fixtures holding both football-data.co.uk
  team rows and FBref player rows, so **League One and Two are invisible to it**, and its ≥8
  threshold leaves smaller errors unexamined. Once ESPN rows exist for all four tiers the same
  audit becomes possible everywhere.
