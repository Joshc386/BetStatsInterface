# Squad membership from the ESPN roster

**Status:** accepted — supersedes ADR 0006's *membership* decision (its form-numbers and
raw-rows/client-aggregation design stand unchanged). Extends ADR 0009's ESPN role a
second time, after ADR 0012.

ADR 0006 derived squad membership from appearances — **Recent squad**, "the squad as it
last took the field" — rather than build a roster source, and knowingly accepted a
consequence: *"a sold player lingers in his old club's panel until he debuts elsewhere in
covered data (the accepted **ghost** — the user will handle these manually)."*

Handling them manually was never going to work, because the ghosts never clear. A player
whose next club is outside covered data has his most-recent *covered* appearance at the
old club **permanently**. Measured on live data (2026-08-22), Wolves' panel held 44
members: 19 had played within 30 days, and **25 were stale — 10 of them over two years
old**, including Diego Costa (last appearance 2023-05-20) and Patrick Cutrone (2021-01-22).
Both moved to leagues we do not cover, so neither would ever have cleared. The list only
grows. ADR 0006 named this as the trigger to revisit: *"remains the documented path if the
staleness ever bites in practice."*

## Decision

**Source Squad membership from each club's ESPN roster, refreshed daily into the existing
`squads` table.** ESPN rather than the FBref squad page ADR 0006 had planned, for two
reasons: FBref's squad tables list players who have *played*, so they cannot answer the
half of the question that matters (an unplayed new signing), and an FBref job is tier-2
work — headful, Cloudflare-gated, VPN-off, supervised — whereas ESPN needs none of that
and runs fully unattended. The `teams.espn_id` plumbing from ADR 0009 already exists.

- **Membership = Squad ∪ anyone with an appearance for the club in the last 30 days.**
  The union is a safety net against an unreconciled name, not a widening. Every one of the
  9 measured false negatives had played within **8 days**, so a short window catches them
  all; a season-long union would additionally keep a January departure until August, which
  is the staleness being removed. An unmatched name therefore degrades the panel to
  *slightly stale*, never to *silently missing a real player*.
- **Identity by a deterministic ladder, then an alias map, then a stamped id.** Full
  normalised name → surname + first initial → surname, each step exact, no fuzzy scoring;
  the surname rung applies **only when that surname is unique within the club's player
  set**, so two Silvas can never collide. A hit stamps `players.espn_id`, after which
  matching never depends on spelling again — the `teams.espn_id` pattern from ADR 0009.
  Residue goes to a deterministic `ESPN_PLAYER_ALIASES` map and is logged. The ladder
  alone resolves 7 of the 9 known misses with no alias work.
- **A Squad member with no appearances is shown, not omitted**, with no figure rather than
  a number, sorted last. "In the squad, nothing known" is genuinely different information
  from "not in the squad" — it is how a new signing becomes visible at all. This changes
  the endpoint contract: a member may now carry zero rows, which ADR 0006's shape never
  allowed.
- **Its own tier-1 scheduled job** (`ingestion/squads.py`, `run_squads.cmd`), alongside
  upcoming / nightly / matchday, with its own log and notifier. Rejected folding it into
  `ingestion.upcoming`: that module owns the fixture slate, and an unresolved *player*
  name must never be able to take the *fixture* slate down.
- **Clubs with no `espn_id` keep Recent squad**, labelled. Non-league cup opponents and
  foreign European clubs have no roster; they degrade to the old behaviour rather than
  showing an empty panel.

## Consequences

- The panel becomes trustworthy for its actual purpose. Birmingham 58 → 26 members,
  Blackburn 69 → 32, Bolton 42 → 27.
- **ESPN is now load-bearing for three separate things** — the fixture slate (0009), cup
  played-detection (0012), and squad membership (this) — while still never being a stats
  source. Every Metric still comes from FBref. An ESPN outage now degrades three surfaces;
  each fails independently and falls back rather than erroring.
- A duplicated human is the failure mode to watch: an unreconciled roster entry shown as a
  no-data member *and* the same player present via the 30-day union. The ladder plus the
  alias map exist to stop it; the roster job logs unmatched names so it stays visible.
- **Squad is only as fresh as the last successful run.** During a transfer window a
  missed day shows yesterday's squad. Acceptable: the union covers anyone actually
  playing, and the job is idempotent and catch-up-safe like the other three.
- ADR 0006's `Recent squad` is not deleted — it becomes the fallback and the safety net,
  and remains the correct description of what appearance-derived membership can and
  cannot know.
