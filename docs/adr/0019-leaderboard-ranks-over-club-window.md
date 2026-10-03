# The Leaderboard ranks over a club window, not the player's own appearances

**Status:** accepted. It records the trade-off behind `CONTEXT.md`'s **Club window** and
**Leaderboard** entries, settled in the grill on 2026-10-03. It adds no table and stores
no derived figure; the window is computed at query time.

Everywhere else in the app, a player's "last N" is **his own** last N Appearances
(`CONTEXT.md` **Appearance**, **Rolling Window**). The Leaderboard deliberately uses a
different window. Without the reasoning, that looks like an inconsistency worth "fixing".

## Decision

**A player's Leaderboard figures are his Appearances inside his club's last 10 League
games that have player data.** He is listed under the club of his most recent League
Appearance, and only his games for that club count. Every list is ranked per 90 over his
minutes in that window, and he needs at least half the window's minutes (450 for 10).

## Why not his own last 10

1. **It ranks people who are not playing.** Measured 2026-10-03: 369 players had 450+
   minutes in their own last 10 League Appearances and a latest Appearance in the Premier
   League. Of those, **114 (31%) had not played in 30 days** and 85 not in 120 days,
   mostly long-term injuries, departures abroad and players who last played last season.
   The board would answer "who did it last spring?", not "who is doing it now?".
2. **The windows cover different stretches of time.** One player's last 10 covers six
   weeks, another's eight months. Side by side, their rates are not measuring the same
   period.

## Why games without player data are skipped

ESPN writes a league team row within hours of the final whistle (`docs/adr/0015`). FBref's
player rows arrive with the next morning's matchday run. On 2026-10-03, 11 of that day's
League One and Two games had team rows and no player rows. If those games counted, every
player at those clubs would look like he had missed a game for about a day: his minutes
and appearance count would drop, and a player near the cut-off would fall off the board.
This is ADR 0016's rule applied to whole games. An unpublished game is not yet known; it
is never a game he missed.

## Alternatives considered

- **His own last 10, plus a recency rule** ("must have played in the last 30 days"). This
  keeps the player page's definition, but a player back from a four-month injury would
  still be ranked partly on games from before it.
- **His club's last 10 League games, counting every game.** Simpler, but wrong for about a
  day after each round, for the reason above.

## Consequences

- **The same player has two different "last 10" figures:** his own Appearances on the
  player page, his club's games on the Leaderboard. Every Leaderboard row names its window
  ("Leeds' last 10 League games: played 6, 480 min") so the two are never confused.
- **A summer signing is measured only on his new club's games.** His earlier games
  elsewhere do not count. That is by design: the window belongs to the club.
- **A promoted club's last 10 League games can include last season's Championship games.**
  That is the app's normal League scope, the same rule the player page follows, and the
  row labels it.
- **A player who leaves drops out on his own.** He stays listed under his old club only
  while its window still holds his games. As the club plays on without him, his minutes
  fall below the cut-off.
