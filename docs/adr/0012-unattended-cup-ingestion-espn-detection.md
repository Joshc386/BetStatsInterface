# Unattended cup and European player ingestion, detected via ESPN

**Status:** accepted — amends ADR 0009 (which scoped ESPN to `STATUS_SCHEDULED` events
only) and builds on ADR 0008's covered-tie filter. Supersedes nothing.

The daily `matchday` task ingests player data for Premier League and Championship only.
Cups and European ties have always required an explicit, typed, supervised run —
roughly 30 a season. `matchday.manual_pending()` was written to report when one was due,
but it **has never fired and structurally cannot**: it counts fixtures that are
`status='finished'` with no player rows, and a cup Fixture is created *by the ingest
itself* (`cups.get_or_create_cup_fixture`), so a tie that has never been ingested has no
row to count. Verified against the full log history — zero occurrences of
`MANUAL RUN NEEDED`. The operator's only real signal was remembering that a cup round
had been played.

The asymmetry is structural, not accidental. League fixtures are flipped to `finished`
by tier-1 nightly from football-data.co.uk (`team_match.ingest`), so `_pending` answers
"played but not ingested?" from the DB alone, at zero network cost — which is why a
quiet day costs ~30s and makes no FBref call. fd.co.uk does not cover cups, so no
equivalent signal exists.

## Decision

**Use ESPN as the "this tie has been played" signal, and let `matchday` ingest cups and
European ties unattended once it fires.** ESPN is unauthenticated, rate-limit-free and
Cloudflare-free, so consulting it daily is cheap in a way an FBref schedule read is not.
The mechanism is deliberately **different per scope**, because the two scopes have
genuinely different shapes:

- **Domestic cups (FA Cup, EFL Cup) — real fixture rows.** `eng.fa` / `eng.league_cup`
  join `ESPN_LEAGUES`, and `upcoming.py` now also ingests **finished** events, setting
  `status='finished'`. This is the amendment to ADR 0009. "Finished" is a **set** of
  status names, not one value — the spike found `STATUS_FULL_TIME`, `STATUS_FINAL_PEN`
  and `STATUS_FINAL_AET` in a single EFL Cup window. Cups are exactly where extra time
  and shootouts happen, so matching only `STATUS_FINAL` would have missed every tie that
  went beyond 90 minutes. Domestic cup fixtures use
  `stage=''`, the same natural key `cups.get_or_create_cup_fixture` uses, so the ESPN row
  is **reused in place** exactly as a league fixture is — no duplicate, no ghost. It also
  buys forward visibility: drawn cup ties appear in the UI's upcoming slate before they
  are played, which signal-only would not give. A postponed tie simply never becomes
  `finished`, so it never registers as pending work — the failure mode that a
  "kickoff has passed" probe would have got wrong.
- **European ties (UCL, UEL, UECL) — signal only, no rows written.** ESPN answers one
  question: *did a covered club play a European tie we have not ingested?* It resolves
  only the covered club — which always has an `espn_id` (verified: 24/24 for 2627) — and
  **never the opponent**. Rejected: writing European placeholder rows. ESPN serves every
  fixture including the foreign-vs-foreign ties **Covered tie** excludes, so placeholders
  would mean alias work for ~250 foreign clubs that hold `fbref_id` but no `espn_id`, on
  the one job that runs fully unattended; and European fixtures are keyed by `stage`
  (ADR 0011), which an ESPN row could never match — producing exactly the ghost rows
  `purge_stale_international_placeholders` exists to clean up. Signal-only makes an
  unfamiliar foreign club **structurally incapable** of blocking anything, and leaves the
  FBref ingest untouched, so **Covered tie**'s promise that the uncovered opponent is
  stored in full still holds.
- **The covered set no longer depends on a single source.** `covered_team_ids` becomes
  the union of a club's PL/Championship `team_match` **and** `fixtures` rows for the
  season, with a logged warning when the two diverge. Historically a no-op. Prompted by a
  live silent failure: with E0 returning HTTP 300 through August 2026, the `team_match`
  set held 24 clubs (Championship only) and **no Premier League club was in scope for
  2627** — an EFL Cup tie between two PL clubs would have been judged out of scope and
  skipped, with nothing logged.
- **Any skipped tie alarms immediately.** `ingestion.cups` currently swallows per-tie
  failures, appends them to `skipped`, and still exits 0 — invisible under automation. An
  automated run now exits non-zero on any skip, firing the existing notifier. This does
  **not** cry wolf on FBref publication lag: `select_covered_games` drops schedule rows
  with no `game_id`, so an unpublished match is never attempted and never recorded as a
  skip. Only genuine failures (unresolvable team, pairing failure, parse error) alarm.

### Verified by spike (2026-08-21)

- **EFL Cup detection works and the gap is real.** `eng.league_cup` served 60 events in a
  -30/+45 day window: 37 already played, of which **19 involve a covered club**. Those are
  ties from 6-8 August that the current system cannot see at all. Covered clubs resolved by
  `espn_id` alone, no aliases needed (Wolverhampton Wanderers → Wolves, Queens Park Rangers
  → QPR).
- **FA Cup is empty this early** (`eng.fa`: 0 events) — its proper rounds start in November.
  Expected, not a fault.
- **European detection reads the main slugs only — never `*_qual`.** `uefa.champions`,
  `uefa.europa` and `uefa.europa.conf` are valid (ESPN returns the right league name) but
  serve 0 events until the league phase starts in September. The qualifying rounds live on
  separate slugs (`uefa.champions_qual` and friends, 51/68/203 events right now). They are
  **deliberately excluded**: the FBref-sourced European data contains **no qualifying stage
  whatsoever** across all 502 fixtures — every stage is group/league phase or later. Reading
  the qualifying slugs would manufacture pending work FBref can never satisfy, which under
  the fail-loud rule below would alarm every single day, forever.

## Consequences

- ESPN's role widens from "upcoming slate" to "has this been played", for domestic cups.
  It remains **never a stats source** — every Metric still comes from FBref.
- A tie that can never be ingested (alias needed, or FBref genuinely lacks the page) is
  re-detected daily and alarms daily until a human resolves it. That is intended: the
  alternative is a silent permanent gap.
- The FBref alias guard is no longer the human-in-the-loop gate it was assumed to be. It
  never was — `backfill_cup_season` catches per-tie exceptions and continues. The
  non-zero exit is what actually puts a human in the loop.
- Unchanged: FBref ingestion is still headful, VPN-off and Cloudflare-gated, so the
  machine must be on and logged in. Automation removes the typing, not that requirement.
- **Pre-existing gap, now explicit:** European *qualifying* ties involving English clubs
  have never been ingested and still will not be. This decision documents that boundary
  rather than changing it.
