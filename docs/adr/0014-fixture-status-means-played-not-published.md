# Fixture status means played, not published

**Status:** accepted — refines the **Fixture** definition in `CONTEXT.md`, extends ADR 0009's
ESPN role a third time (after 0012 and 0013), and generalises the source-outage doctrine
already recorded under **Covered tie**.

`CONTEXT.md` defined a finished **Fixture** as *"one with results in"*. That was never a
decision, only an accurate description: for league competitions the *only* writer of
`status = "finished"` was `team_match.py`, fed by the football-data.co.uk CSV, so "the match
was played" and "we hold its results" arrived as one indivisible fact.

They are not one fact, and on 2026-08-23 the difference cost real data. football-data.co.uk
had not published `E0.csv` for 2026-27 (a genuine 404 — Apache's `mod_speling` dresses it as
HTTP 300 with a list of near-miss filenames). No CSV meant no `team_match` rows, which meant
no Premier League Fixture was ever marked finished. `run_backfill._pending` counts only
finished Fixtures, so it returned **0**, and `ingestion.matchday` reported *"no pending player
data for 2627"* and **exited clean** for four consecutive mornings.

The damage was not confined to the source that failed. **FBref's player pipeline was
withdrawn from the Premier League by football-data.co.uk's silence** — two sources chosen
precisely so they would fail independently, coupled through a status column nobody intended
as a coupling. Measured at the time: 6 Premier League Fixtures played with zero player rows,
and 11 Championship Fixtures likewise, the latter entirely invisible because that league
*had* published — just only 12 of 23 games.

## Decision

**`finished` means the match was played** — kickoff happened and it reached a result *in the
world*. It asserts nothing about whether we hold data. Concretely:

- **Any source that can see a result may assert `finished`.** `ingestion.upcoming` therefore
  takes finished events for **leagues** as well as cups. The discriminator in
  `scoreboard_window` / `parse_scoreboard` therefore changes from "is this a cup?" to "is this
  *not* international?" rather than gaining a branch. **Internationals stay scheduled-only**,
  and that exception is load-bearing: ADR 0011's placeholders are ephemeral, and
  `purge_stale_international_placeholders` deletes only `scheduled` past rows — so a
  placeholder marked finished would never be purged and would linger as exactly the ghost that
  function exists to prevent. The lookback becomes a uniform 30 days for everything that takes
  finished events. This is one request per league either way, and it
  doubles as catch-up after a missed run. ESPN remains a **slate** source and **never** a
  stats source — not one Metric moves, and football-data.co.uk keeps owning league results.
  The existing writers (`team_match`, `cups`, `players`) are all kept: together they form a
  union, so no single source's outage can again withdraw a Fixture from the pipeline.
- **Publication is derived, never stored.** "Has this source published?" is answered by a
  join — is there a **Team-Match** row? a **Player-Match** row? — so it cannot drift out of
  sync with the rows actually present, which a stored per-source flag can.
- **Overdue is judged per Fixture per source** (see `CONTEXT.md`), reusing the ingesters' own
  scope constants to decide what each source owes. A League One Player-Match is not Overdue;
  it is out of scope by design.
- **Two-tier reporting.** One query split by age: recently Overdue **alarms**; older gaps
  become a standing never-alarming **known-gaps** count. Nothing is written off silently.
- **Each job audits the source it owns** — `nightly` for football-data.co.uk team rows,
  `matchday` for FBref player rows, `upcoming` for the slate itself. No fifth scheduled job,
  and no alarm can misattribute one source's failure to another's.
- **A postponement is distinguished by asking ESPN at run time.** A match postponed with no
  new date stays past-dated and `scheduled` — correct under the new definition, but
  indistinguishable from "nothing marked it finished". `upcoming` already holds the payload
  that says `STATUS_POSTPONED`, so the check costs no extra request and needs no third status.
  A *rescheduled* match needs nothing: `_upsert_fixture` keys on
  `(competition, season, home, away)` and updates the date in place.

## Considered options

- **Add a third status, `played`** — keeps `finished` literally meaning "results in".
  Rejected: a migration plus a third state every consumer must reason about, to preserve a
  definition that was descriptive shorthand rather than an intended constraint.
- **Per-source publication flags on the Fixture** — closest to a literal reading of the
  five-stage pipeline. Rejected as machinery for facts already derivable by a join, and it
  introduces flags that can disagree with the rows present.
- **Keep the league-level check and tighten its grace period** — smallest possible change.
  Rejected: `unexpected_skips` is structurally blind to *partial* publication, so it would
  have caught the Premier League's total absence (it did) and still missed all 11 Championship
  Fixtures (it did).
- **A date-based fallback in `_pending`** (treat kickoff + N hours as ingestable) — needs no
  new source at all. Rejected: it guesses where ESPN gives a real signal, and a postponed
  match would become permanent phantom pending work.
- **Fold every audit into `matchday`** — one place to look. Rejected: a late
  football-data.co.uk CSV would make *matchday* exit non-zero, reproducing the exact
  ambiguous-health-signal problem this ADR exists to remove.

## Consequences

- **The two pipelines genuinely decouple.** football-data.co.uk lateness now degrades team
  data only; FBref player ingestion proceeds on ESPN's word that the match was played.
- **A Fixture may now be `finished` with no data at all**, briefly and legitimately. Nothing
  downstream is affected: the only API consumer of `status` also bounds on `date >= now`, so
  past Fixtures flipping to finished are invisible to the API and UI. This is contained to
  the ingestion layer.
- **ESPN is now load-bearing for four things** — the fixture slate (0009), cup played-detection
  (0012), Squad membership (0013), and league played-detection (this). It is still never a
  stats source. An ESPN outage now means new results are not *noticed* until it returns;
  because the finished-writers are a union, football-data.co.uk still marks league Fixtures
  finished as it publishes, so the degradation is a delay, not a stall.
- **Alarm volume rises before it falls.** The first per-fixture audit will surface the known
  ~79 international gaps and the 17 current ones. The tier split keeps the recurring noise to
  genuinely actionable items.
- **The known-gaps count is a coverage figure**, and should be surfaced honestly rather than
  quietly tolerated — consistent with the project's standing rule about implying completeness.
- `unexpected_skips`' 36h `PUBLISH_GRACE` was tuned for whole-league lateness at a season
  boundary. Per-fixture grace is a different measurement and its value is set empirically once
  the audit has run against real data; it is deliberately not inherited unexamined.
