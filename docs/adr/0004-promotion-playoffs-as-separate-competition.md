# Promotion play-offs as a separate competition

**Status:** accepted

The Championship player backfill (ADR 0003) reused `read_schedule` → `link_fixtures`
verbatim. FBref's Championship schedule includes the **EFL promotion play-offs**
(`round = "Promotion play-offs — Semi-finals" / "— Final"`): 4 two-legged semi-final
legs plus a final, per season. `link_fixtures` matched each scheduled game to an
existing league Fixture on the natural key `(competition_id, season, home_team_id,
away_team_id)`. A play-off leg has the **same home/away orientation** as that
season's league meeting between the two clubs, so it matched the **league** Fixture
and overwrote its `fbref_match_id`, after which `ingest_match` wrote the play-off
match's player rows onto the regular-season Fixture — mis-tagged `club_league`.

This was found during the post-backfill data QA: a cross-source invariant
(`sum(player_goals) ≤ team_match.gf` per team-fixture, since FBref credits no player
for an opponent own-goal) was violated on 7 Championship team-fixtures — e.g. a
league row recording *Leeds 1-0 Norwich* carried the four scorers of the *4-0
play-off semi-final*. 15 league Fixtures across 2023-24/24-25/25-26 were affected
(5 play-off games × 3 seasons); the Premier League was clean (no play-offs).

## Decision

**Model the play-offs as their own competition — "Championship Play-offs",
Competition Type `club_cup` — never as part of the regular season.**

- `link_fixtures` branches on the schedule `round` column. Regular-season rows
  (`round` = the competition name, e.g. `"Championship"`) link the existing league
  Fixture as before. Play-off rows (`"play-off"` in `round`) **get-or-create their
  own Fixture** under the play-off competition; the differing `competition_id` keeps
  the natural key from colliding with the league meeting. Leagues with no play-offs
  (the Premier League) pass `playoff_competition=None`, so the branch is a no-op and
  such rows surface as `unmatched`.
- One backfill pass covers both: `_pending_fixtures` spans the league **and** its
  play-off competition, and `ingest_match` tags each row with **its own Fixture's**
  competition type rather than a single league type.
- Play-offs are **player-data only**. football-data.co.uk does not cover them, so
  there is no `team_match` for a play-off Fixture; team form stays league-sourced.

## Considered options

- **Filter the play-offs out of the schedule entirely** — rejected. It discards
  real, cached player data and still leaves "what about play-off form?" unanswered.
  Segregating preserves the data and satisfies the scope-tagging non-negotiable.
- **Add a dedicated `competition_type` (`club_playoff`)** — rejected for v1. It is a
  domestic knockout; `club_cup` fits without an enum migration touching every
  enum-handling site. Revisit only if play-off form needs to be distinguished from
  cup form in the UI.
- **Also derive `team_match` for play-off games from the FBref scorelines** —
  deferred. Player data is the immediate need; team-level play-off rows can be added
  later (FBref is the sanctioned `club_cup` team source per ADR 0001) without
  reworking this decision.

## Consequences

- A standing **regression guard** (`tests/test_reconciliation.py::
  test_player_goals_never_exceed_team_goals`) asserts the cross-source invariant
  over the whole DB, so any future orientation-collision contamination fails CI.
  A unit test asserts `link_fixtures` routes a play-off row to the play-off
  competition and leaves the same-orientation league Fixture untouched.
- **Remediation of the existing 3 seasons was a non-destructive relabel**, not a
  delete: the 455 mis-parked player rows were `UPDATE`d onto the 15 newly-created
  play-off Fixtures (`competition_id`, `competition_type`, `date` corrected); the 15
  league Fixtures were re-linked to their real regular-season `game_id`s. Those 15
  regular-season league games now have **no player rows** until a one-off VPN-off
  backfill of 2324/2425/2526 fetches the real league pages (their FBref pages were
  never cached, because the collision fetched the play-off page in their place).
- The pattern generalises: League One / League Two also have promotion play-offs, so
  when their player data is attempted each needs its own `"… Play-offs"` competition
  seeded and passed to `link_fixtures`.
- Extends ADR 0003's lesson — a second way the FBref schedule can mis-map onto
  football-data.co.uk Fixtures (the first being two-spelling team names): a single
  natural-key orientation can denote **two different matches** in one season.
