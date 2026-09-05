# Live-season play-off ingestion, triggered by the ESPN season slug

**Status:** accepted — extends ADR 0004 (play-offs as their own competition) by giving
them a live-season trigger, applies ADR 0012's ESPN-as-signal pattern to a new scope,
and amends ADR 0009's league slate (which routed every league event to the league
competition). Supersedes nothing.

ADR 0004 modelled the promotion play-offs correctly and ADR 0012 made cups
self-scheduling, but nothing connected the two: **in a live season the play-off
fixtures never come into existence at all.**

The only thing that creates them is `players.link_fixtures`, reading FBref's league
schedule during a backfill. That backfill only launches when
`run_backfill._pending()` > 0 for the parent league — and that count reaches 0 the
moment the last league round is ingested, in early May. The play-off semi-finals are
played a week later. So:

- **No fixture row exists**, therefore `coverage.py` has nothing to audit, therefore
  nothing appears in the digest. The gap is silent by construction — not a known gap
  that alarms quietly, but one the system is structurally unable to see.
- **A manual run does not rescue it either.** `run_backfill.run` gates on
  `pending == 0 -> "nothing pending — done"` and never launches `players.py`, so
  `run_backfill 2526 "Championship"` in May exits successfully having done nothing.
- Historically the play-offs only ever landed because a **retrospective whole-season
  backfill** ran months later. Championship Play-offs holds all 5 fixtures for all six
  seasons; that came from the July 2026 sweep, not from live operation.

Two consequences were live in the DB when this was written: **League One Play-offs
2526 — 5 fixtures, 0 player rows**, and **League Two Play-offs — 0 fixtures at all**
(those competitions were seeded on 2026-08-26, after League Two's link run, so its five
play-off rows went to `unmatched` and were discarded).

The signal we needed was already arriving daily and being thrown away. ESPN's `eng.2`,
`eng.3` and `eng.4` scoreboards each carry the five play-off matches. `upcoming.py`
upserted them into the **league** competition, where each collapsed onto the
already-finished league fixture with the same pairing (`skipped_finished`) — two clubs
in a division have always met both ways, so the natural key always hits. That is why
Championship 2526 holds exactly 552 rows with no contamination: benign, but the trigger
was being discarded.

## The discriminator, and what the evidence forced

`event.season.slug`, sampled across the full 2025-26 season:

```
eng.1            {'2025-26-english-premier-league': 380}
eng.2            {'regular-season': 552, 'promotion-semifinals': 4, 'promotion-final': 1}
eng.3            {'regular-season': 552, 'promotion-semifinals': 4, 'promotion-final': 1}
eng.4            {'regular-season': 552, 'promotion-semifinals': 4, 'promotion-final': 1}
eng.fa           {'first-round': 40, 'second-round': 20, ... 'final': 1}
eng.league_cup   {'preliminary-round': 2, 'first-round': 35, ... 'final': 1}
```

**The slug vocabulary is not shared between competitions.** The obvious rule —
"anything that is not `regular-season` is a play-off" — would have routed all 380
Premier League fixtures into a play-off competition, because eng.1 uses a
season-scoped slug (`2025-26-english-premier-league`) that also changes every year. The
cups have a third vocabulary again. Any rule phrased as a negation of `regular-season`
is therefore wrong, and any allowlist containing eng.1's slug breaks at every season
rollover — the once-a-year trap class that already cost five days of Championship data
via the stale `seasons_*.html` cache.

## Decision

**Route play-off events to the play-off competition on a positive slug match, gated on
the league actually having one.**

- `PLAYOFF_SEASON_SLUGS = {"promotion-semifinals", "promotion-final"}` routes an event
  to `"<league> Play-offs"`. Everything else goes to the league exactly as before. The
  match is **positive**, never a negation, so an unrecognised vocabulary cannot capture
  a whole competition.
- The branch is gated on the `"<league> Play-offs"` competition **existing in the DB** —
  the same test `players.backfill_season` already applies when deciding whether to pass
  `playoff_competition` to `link_fixtures`. The Premier League has no such row, so eng.1
  never enters this code path and its season-scoped slug is irrelevant. The gate is data
  driven: seeding a play-off competition is what turns the routing on.
- The check lives in the **league branch only**, never in shared `parse_scoreboard`. The
  cup vocabulary (`first-round`, `quarterfinals`, …) would trip it on every run.
- **An unrecognised slug in a division that has play-offs is skipped, reported, and ends
  the run non-zero** — it is never written to either competition. Nothing is guessed.
- `run_backfill._pending` counts the `"<name> Play-offs"` sibling alongside its parent
  league, mirroring the `comp_ids` that `players.backfill_season` has always built. A
  competition with no sibling (every cup, the Premier League) is unaffected.
- `matchday` builds play-off `team_match` rows after a league run, zero-network, via the
  same `cups.backfill_cup_team_match` pass the cups use. This finally automates what ADR
  0004 deferred and ADR 0008's follow-up had been doing by hand.

The chain that results is exactly ADR 0012's, applied to a new scope: **ESPN writes the
finished fixture -> the existing zero-network DB probe sees it -> matchday runs the
league -> `backfill_season` links and ingests it**, because that function has included
the play-off competition in its `comp_ids` since ADR 0004. The fix makes the trigger
agree with an ingester that was already ready.

## Considered options

- **Fail loud on an unrecognised slug (raise).** Rejected. `ingest_upcoming` iterates
  `ESPN_LEAGUES` in order and a raise aborts the whole run; Championship is second, so
  one bad slug would cost League One, League Two, the World Cup and both domestic cups
  their slate that day, after which `squads` (09:00) derives its club list from a stale
  slate and matchday's finished-marking stops. The project already weighed this exact
  trade-off for cups and chose reporting over rollback: *"losing a whole cup slate over
  one non-league spelling would take the fixture view down with it."* The unknown-**team**
  case raises for a reason that does not apply here — with no `team_id` there is no row
  it *can* write. With an unknown slug both clubs, the date and the status are known and
  only the destination competition is in doubt.
- **Fall through to the league on an unrecognised slug** (today's behaviour). Rejected.
  It is safe only for play-off matches specifically, where the pairing already exists so
  the row collapses. For a genuinely new phase it would create a real extra fixture
  tagged `club_league`, violating the scope-tagging non-negotiable and poisoning
  "last N games" silently. An absent row with an alarm beats a wrongly-tagged row
  without one.
- **A date-window heuristic** (treat leagues as pending during May). Rejected: a
  hardcoded calendar in an otherwise data-driven system, and it re-runs blind.
- **An ESPN probe at plan time in `matchday`**, mirroring `espn_pending` for Europe.
  Rejected as second-best: it works, but it puts play-off knowledge in the orchestrator
  rather than in the ingester that owns fixture rows, and it would re-derive daily what
  a fixture row states once.
- **Parsing `competitions[0].notes`** ("1st Leg" / "2nd Leg"; the final carries none).
  Rejected — string parsing of display text where a structured field already exists.

## Consequences

- Play-offs are ingested in the season they are played, by the unattended slate plus the
  ordinary supervised matchday run. No new job, no new schedule entry.
- **The unknown-slug branch should never fire.** It is a tripwire for ESPN renaming a
  slug, which is otherwise exactly how this trigger would be lost again without anyone
  noticing — the failure this ADR exists to remove, returning undetected.
- A play-off fixture written by ESPN and one created later by `link_fixtures` share the
  natural key `(competition_id, season, home_team_id, away_team_id)` that
  `_link_playoff_fixture` already gets-or-creates on, so the FBref pass stamps the ESPN
  row rather than duplicating it. One row, never a ghost.
- Historical play-off rows still arrive the old way: `link_fixtures` creates them during
  the L1/L2 backfill, so League Two Play-offs 2526 is created and League One Play-offs
  2526 is filled by that run, not by this change.
- The Premier League is untouched by every part of this, and remains the case that
  proves the gate: no play-off competition, no routing, no slug check.
