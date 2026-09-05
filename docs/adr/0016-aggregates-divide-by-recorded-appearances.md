# Aggregates divide by Recorded Appearances

**Status:** accepted — adds **Recorded Appearance** to `CONTEXT.md` and amends its
**Summary Metric** entry. Applies the sparsity doctrine ADR 0011 accepted at ingest to the
query side, and corrects a claim in `CLAUDE.md`.

This is not a new contract. `stats.py` already had one, stated in a test docstring since the
module was written:

> *"NULLs are excluded from total/average but still counted as games, which is stats.py's
> actual contract (a sparse row shrinks the sample rather than scoring zero)."*
> — `tests/test_stats.py:39`

`average` implements it. `hit_rate` implements it, and publishes the denominator it used as
the *N* in "x of N (%)". `per_appearance` and `per_90` were added later and implement neither:
they divide by every row in the window, which counts a game the source never measured as a
game in which the player did nothing.

## What a NULL Metric actually means

It means **the source did not publish that column for that match**. Verified across all
319,006 `player_match` rows, not inferred:

- `shots`, `sot`, `tackles`, `fouls_drawn`, `fouls_committed` are **always NULL together** —
  zero rows where they disagree. `minutes`, `goals`, `assists`, `yellows`, `reds` and
  `carded` are **never** NULL.
- Of 10,617 fixtures with player rows, **9,253 are fully populated, 1,364 fully NULL, and
  none is mixed.** A missing column is a property of the *page*, never of the player.

That second fact is what forecloses the alternative reading. If NULL meant "took no shots" it
would vary between players in the same match. It never does.

`CLAUDE.md` attributes this to *"34 FA Cup fixtures… a per-page quirk"*. The FA Cup figure is
exact but it is 2.5% of the phenomenon:

| competition | scope | fixtures |
|---|---|---|
| World Cup Qualifiers | international | 890 |
| AFCON Qualifying | international | 374 |
| Asian Cup Qualifying | international | 40 |
| FA Cup | club_cup | 34 |
| Conference League | club_european | 26 |

**95.6% is minor-confederation international qualifiers** — precisely the sparsity ADR 0011
accepted knowingly: *"Metric sparsity on minor-confederation pages is accepted and surfaced
as NULL … never patched from lesser sources."* Scoring those games as zero patches them with
a value no source gave, which is the thing that ADR ruled out.

## What it cost

The default scope is all competitions (`entity_summary(competition_id=None)`), so a player's
international caps land in his club form. Shots per 90, as shown versus over the games where
shots were actually recorded:

| player | unrecorded / all | shown | recorded-only | understated |
|---|---|---|---|---|
| Koulibaly | 31 / 84 | 0.263 | 0.430 | 64% |
| Hakimi | 26 / 71 | 0.948 | 1.468 | 55% |
| Aguerd | 37 / 113 | 0.441 | 0.657 | 49% |
| Aubameyang | 19 / 99 | 2.119 | 2.798 | 32% |

The error scales with a player's international caps, so it is worst for exactly the
best-known players — the ones most likely to be looked up. Five of the eleven player Metrics
are affected; the other six are never NULL.

## Decision

**Every aggregate divides by Recorded Appearances** — the Appearances whose page published
*that* Metric. The denominator is metric-dependent: the same window gives shots a smaller
sample than goals.

**The denominator is published beside the figure.** `games` and `minutes_total` keep meaning
what they say — every Appearance and every minute in the window, metric-independent, so
"Minutes" does not move when you switch metric. Alongside them, `recorded_games` and
`recorded_minutes` carry what the ratios actually divided by. Without this, `total /
minutes_total × 90` no longer equals `per_90` and nothing on screen explains the gap.

This mirrors the shape `frontend/src/lib/aggregate.ts` has always returned for the Fixture
view (`{ games, n, total, average, … }`) and the shape hit-rate has always reported. The
backend aggregate mode was the one place the pattern was missing.

`recorded_games` applies to **both entities**: 2,716 `team_match` rows (7.9%) carry a NULL
Metric, so team averages were already computed over fewer games than the window reported. No
team figure changes — team `average` was always correct — only its denominator becomes
visible. `recorded_minutes` stays player-only; teams have no minutes.

**`average` becomes team-only.** With the fix, player `per_appearance` and `average` are the
same number, and `CONTEXT.md` has only ever given players *total / per-Appearance / per-90*.
It joins `per_appearance`, `per_90` and `minutes_total` as an entity-specific field.

## Considered and rejected

- **Keep counting unrecorded games (status quo).** Requires reading per-90 as "output per
  minute on the pitch, counting unmeasured minutes as barren" — a defensible statistic, but
  not the one the label promises, and it silently disagrees with `average` and `hit_rate` on
  the same screen.
- **Drop per-90 for players.** Cheapest, but per-90 is the only aggregate that correctly
  normalises the mixed game lengths `CONTEXT.md` flags under **Metric** — a cup tie that went
  to extra time contributes 120-minute figures.
- **Redefine `games`/`minutes_total` as the metric-scoped denominators.** Keeps the arithmetic
  checkable with no new fields, but "Appearances" would then read 53 for shots and 84 for
  goals on one window, making the window itself feel unstable.
- **Backfill the missing columns from another source.** Ruled out by ADR 0011 and by the
  Metric entry's rule that a correction needs two independent sources agreeing against a
  third.

## Consequences

- Published figures change for any player with caps in affected competitions. Per-90 and
  per-appearance go **up**, sometimes a lot. This is a correction, not a regression, and the
  numbers in the table above are the regression fixtures.
- A window can now report `games: 84` and `recorded_games: 53` and both are true. The UI must
  make that legible rather than looking like an inconsistency.
- Sample sizes shrink for the five affected Metrics, which is honest: a per-90 over 53 games
  is what the data supports. Where the whole window is unrecorded the figure is `None`, as
  now.
- `CLAUDE.md`'s "34 FA Cup fixtures" note needs correcting to name internationals as the
  dominant cause.
