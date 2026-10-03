# Referee figures come from FBref player rows, not team rows

**Status:** accepted. It records the trade-off behind `CONTEXT.md`'s **Referee** and
**Cards** entries, settled in the grill on 2026-10-03. It adds `referees` and
`fixtures.referee_id` (migration `0009`) and stores no derived figure.

The Referee hub shows a referee's per-match Cards, yellows, reds and fouls. Two tables could
supply those numbers, and they disagree. This records why the hub reads one and ignores the
other, because it looks wrong without the reasoning. A referee's yellows for a league match
can differ from the yellows on that same match's team page.

## The two candidate sources

- **`team_match`**: one row per side, already holding `yellows`, `reds` and `fouls`. Its
  source changes with scope. League rows come from football-data.co.uk (`source='fdcouk'`).
  Cup, European and international rows are summed from FBref player rows by `cups.py` and
  `internationals.py`.
- **`player_match`**: one row per player, always from FBref, in every scope. It comes from
  the same match page that names the referee.

## Decision

**A referee's figures are summed from both sides' `player_match` rows, in every scope.**
Cards, yellows, reds and fouls are computed per Fixture at query time and never stored. The
page labels the difference from the team pages; nothing reconciles it silently.

- **Cards** = per player, `yellows + reds - (1 if reds > 0 and yellows >= 2)`. FBref records
  a two-yellow dismissal as 2 yellows + 1 red (715 of 715 league rows with 2 yellows carry a
  red). `second_yellows` is NULL in every row because `summary` does not publish `2CrdY`,
  so the second-yellow red is derived rather than read.
- **Fouls** = the sum of `fouls_committed`. It divides only by Fixtures whose page published
  fouls (ADR 0016). Yellows and reds are never NULL (0 of 518,649 rows), but fouls is NULL on
  41,825 rows, always for a whole page at a time.
- A Fixture he refereed that has **no player rows at all** counts in his match total but in
  no rate. That is 81 of 17,548 finished Fixtures, 77 of them minor internationals.

## Why not the team rows

1. **League team rows hide exactly the bookings that matter.** football-data.co.uk records
   a two-yellow dismissal as 1 red and 0 yellows. Across league team-sides, the two sources
   disagree on yellows for **3.6%**, and **88%** of those had a red (measured 2026-09-29). A
   referee page asks how readily he books players, and that convention removes the second
   booking of every player he sends off.
2. **The team rows mix sources by scope.** Reading `team_match` would put football-data.co.uk's
   convention on league matches and FBref's on everything else. The hub's scope toggle
   (League / Cups / Europe / International / All) would then change the counting rule as well
   as the sample.
3. **The referee is named on the FBref page.** His identity and his figures come from one
   document, so a referee's record never depends on two sources agreeing that a match took
   place.

## Alternatives considered

- **Team rows for league matches, player rows elsewhere.** This keeps the referee page equal to
  the team page on league matches, but brings back point 2: one referee, two counting rules,
  switched by a toggle.
- **Reconcile the two per Fixture**, for example taking the larger figure. Ruled out by the
  `CLAUDE.md` rule against silently working around a source, and by `CONTEXT.md`'s **Metric**
  rule that a correction needs a third source agreeing.
- **Store per-Fixture referee totals.** Not needed. A referee window is at most a few hundred
  Fixtures, and `CLAUDE.md` says to compute at query time and materialise only for a real
  performance problem.

## Identity

No source gives a referee id. A referee is **the name as the match page prints it**, plus
the hand-confirmed `REFEREE_ALIASES` in `ingestion/referees.py`. They are never merged on a
fuzzy match. The backfill prints near-duplicates for review and decides nothing about them.
This is the same rule as for player and ESPN names. Andy and Bobby Madley are brothers, and
both referee.

## Consequences

- On a league match with a second-yellow dismissal, the referee page shows one more yellow
  than the team page, by design. Both pages say which source they count from.
- On any match with a second-yellow dismissal, yellows + reds is one more than Cards per
  dismissal. That is the **Cards** definition, not an error.
- A referee's figures exist only where FBref player rows exist. Every finished Fixture is
  FBref-linked today, so in practice this means the 81 Fixtures with no player rows.
- Referees are captured at FBref ingest by the league, cup and international paths. The
  league default and the covered-ties-only label for UEFA-appointed foreign referees are
  query-side rules (`CONTEXT.md` **Referee**). They are not filters at ingest.
