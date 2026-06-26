# Football Betting-Research Platform — Context

The shared vocabulary for a personal, single-user web app for football betting *research*.
It serves rolling-window team/player statistics from a local Postgres DB populated by
scheduled ingestion jobs. It is a research tool — not a bot, not a predictive model.

## Language

### Entities & facts

**Fixture**:
A single scheduled event between two teams — one row, a date, a status (`scheduled` | `finished`). Both upcoming and historical games are Fixtures; a finished Fixture is one with results in. The unit the **Fixture view** is built around.
_Avoid_: match (ambiguous), game (UI label only).

**Team-Match**:
One team's perspective on a **Fixture** — the team-level fact row. A Fixture has exactly two Team-Match rows. The unit a team **Rolling Window** iterates over.

**Player-Match**:
One player's perspective on a **Fixture** — i.e. an **Appearance** (minutes > 0). 0–N per side per Fixture. The unit a player **Rolling Window** iterates over.

### Stats & summaries

**Metric**:
A raw, measurable per-game quantity recorded on a fact row — e.g. shots, shots on target, tackles, fouls won, cards, minutes. This is what is *stored*.
_Avoid_: stat (informal), market.

**Summary Metric**:
A **Metric** summarised over a **Rolling Window**. Computed at query time, never stored. Has two modes:
- _aggregate_ — for players: total, per **Appearance**, and per-90 (minutes-normalised); for teams: total and per-game. No threshold.
- _hit-rate_ — count of games whose Metric value clears a **Threshold**, expressed as "x of N (%)".

**Appearance**:
A single game in which a player played > 0 minutes — i.e. a game with a `player_match` row (FBref records no row for a player who did not feature). The player **Rolling Window** counts appearances, not team fixtures: "last 5 games" means his last 5 appearances. `minutes` is displayed per game and as a window total; the minutes value distinguishes a start from a cameo (no separate start flag is stored).

**Threshold**:
The user-chosen value a **Metric** is tested against, per game, in a Summary Metric's hit-rate mode (e.g. "2+ shots on target"). Tested at the per-game level, never against an average. Adjustable in the UI per lookup, so the resulting per-game boolean is computed at query time and never stored.
_Avoid_: line, market.

**Rolling Window**:
The set of past games a Summary Metric is computed over. Selectable two ways:
- _game-count_ (default) — "last N games", a SQL window function over date-ordered rows.
- _season_ — "this season" / "last N seasons", a filter over the `season` tag then aggregate.

**Breakdown**:
The per-game rows underlying a Summary Metric (date, opponent, H/A, Metric value), shown with a footer total/average so the headline and evidence reconcile. It is the source data the headline aggregates, not extra work.

**Head-to-Head** (H2H):
The set of past **Fixtures** between two specific teams — a **Rolling Window** filtered to one opponent. Drives the **Fixture view**'s meetings list and its aggregate Summary Metrics ("BTTS in 4 of the last 6 meetings"). Fixture-level Metrics (BTTS, total goals) give one figure per meeting; per-team Metrics (goals-for, clean-sheet) differ by side. League-only in v1 (no cup/international team data); spans all held seasons, and degrades gracefully when two teams have few or no meetings.
_Avoid_: form (form is measured vs all opponents; H2H is vs one specific team).

### Scope & coverage

**Competition Type**:
The scope tag on every fact row, one of `club_league` | `club_cup` | `club_european` | `international`. "Last N games" is meaningless without it — a window is always read within a scope.

**v1 scope**:
The top four English tiers — Premier League, Championship, League One, League Two — `club_league` only. **Team** data is confident across all four (football-data.co.uk). **Player** data is confident for PL + Championship but **best-effort for League One / League Two** (Opta's lower-tier coverage was always thin, compounded by FBref's Jan-2026 removal) — verified at backfill and labelled in the UI as "covered competitions only", never implied complete.

**Promotion Play-offs**:
The end-of-season knockout deciding the last promotion place (Championship/League One/League Two): teams finishing 3rd–6th contest two-legged semi-finals (4 fixtures) then a single neutral-venue final. Modelled as its **own competition** ("Championship Play-offs", **Competition Type** `club_cup`), never as part of the regular season — a play-off leg shares the same home/away orientation as a league meeting, so keeping it under `club_league` collided on the Fixture natural key and contaminated league form (see `docs/adr/0004`). Player data only (football-data.co.uk does not cover play-offs). A "last N **league** games" window therefore excludes them by scope.
_Avoid_: treating a play-off game as a Championship (league) Fixture.

**Squad** (roster):
The set of players currently registered to a club, as listed on that club's FBref squad page — *membership*, not appearance history. A new signing who has not played yet is in the Squad; a player loaned out is not (he appears at his loan club). Maintained by the roster-refresh job (Job C) into the `squads` table. Distinct from a **Player-Match**/**Appearance** (what a player *did*): the Squad decides *who* is shown, Appearances supply *the numbers*.
_Avoid_: lineup (a Squad is not a starting XI), roster as a synonym for the played-minutes set.

**Squad form** (not lineup):
The fixture view shows recent Summary Metrics for every player in the **Squad** — membership comes from the roster (FBref squad page), the form numbers from each player's **Appearances**. Never an auto-predicted XI. Filtering to confirmed starters is a manual user step once the official lineup is released. (FBref's squad page handles loans only roughly; precise loan/contract status via Transfermarkt is out of scope for v1.)

### Out of scope

**Market**:
A bookmaker's priced offering — *a thing one would bet on*. Deliberately **not modelled or stored** in this project. The platform surfaces the user's own metrics and hit rates against a self-chosen Threshold; it does not ingest, store, or reason about bookmaker odds.
_Avoid_: using "market" to mean a Metric or a Summary Metric.

## Relationships

- A **Fixture** has exactly two **Team-Match** rows and 0–N **Player-Match** rows per side.
- A **Team-Match** / **Player-Match** belongs to exactly one **Fixture**.
- A **Summary Metric** is computed from one **Metric** over one **Rolling Window**.
- A **Summary Metric** in hit-rate mode requires one **Threshold**; in aggregate mode it requires none.
- Every **Metric** row carries a **Competition Type** and a `season`; a **Rolling Window** is always read within a Competition Type scope.
- A **Breakdown** is the set of **Metric** rows a **Summary Metric** aggregates.
- A **Head-to-Head** is a **Rolling Window** filtered to one opponent; its **Breakdown** is the two teams' past meetings.
- The **Fixture view** compares two teams by **Team form** (each team's recent Summary Metrics vs all opponents) and **Head-to-Head**; the **Team hub** is one team's full deep-dive. Both are read-only surfaces over the same facts, not stored entities.

## Example dialogue

> **Dev:** "When you say a player is 'good for cards', do you mean his average or his hit rate?"
> **Domain expert:** "Hit rate — carded in 4 of his last 5. The average hides streaks. The threshold is per game; I want to know how often he clears it, not the mean."
> **Dev:** "And 'last 5' — last 5 league games, or any competition?"
> **Domain expert:** "Always within a scope. Last 5 *league* games. A cup game is a different Competition Type — don't mix them into the window."

## Flagged ambiguities

- **"Market" vs "Metric"** — the original spec used them interchangeably. Resolved: a **Metric** is the raw stored quantity; a **Summary Metric** is its rolling headline; a **Market** is a bookmaker offering and is out of scope. The threshold object is a **Threshold**, not a "market".
- **Odds** — the spec stored closing odds (`match_odds`). Resolved: **odds are out of scope entirely** — not modelled and not stored. `match_odds` is dropped from the data model.
- **"Bookings"** — the spec defined `bookings = yellows + reds`, which double-counts a two-yellow sending-off (`CrdY=2, CrdR=1` → 3). Resolved: store **`yellows`** and **`reds`** raw; the disciplinary unit for hit-rate is **`carded`** = `yellows > 0 OR reds > 0` (a per-game boolean — a player either saw a card or didn't). No stored `bookings`. A numeric is deferred; if added, `cards_shown = yellows + (reds − second_yellows)` using FBref's `2CrdY` to avoid the double-count.
