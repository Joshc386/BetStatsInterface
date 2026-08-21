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
A raw, measurable per-game quantity recorded on a fact row — e.g. shots, shots on target, tackles, fouls won, cards, minutes. This is what is *stored*. A Metric covers the game **as played**: a cup tie that went to extra time contributes whole-match (120-minute) figures — FBref reports no 90-minute split — while a league game is 90 minutes by nature. A Cups-scope **Rolling Window** therefore mixes game lengths; thresholds conventionally quoted for 90 minutes (e.g. corners lines) should be read with that in mind.
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

**Spell**:
A player's continuous run of **Appearances** at one club, bounded by a transfer. The **player view** delimits spells with a separator and subtotals each; *Segment by Team* groups the window's appearances into spells (*Segment by Competition* groups the same appearances by competition instead). A spell is read straight from the data — the `team_id` changing across a player's date-ordered **Player-Match** rows — not from any roster source. Subtotals always reconcile to the headline (they partition the same rows).
_Avoid_: stint; "career" (a career is all of a player's spells).

**Head-to-Head** (H2H):
The set of past **Fixtures** between two specific teams. Drives the **Fixture view**'s meetings list and its aggregate Summary Metrics ("BTTS in 4 of the last 6 meetings"). Fixture-level Metrics (BTTS, total goals) give one figure per meeting; per-team Metrics (goals-for, clean-sheet) differ by side. Spans all held seasons and **every team-level scope on record**: league meetings plus domestic-cup ties (FA Cup, EFL Cup, play-offs — cup team data from 2023-24 onward), each meeting labelled by competition and filterable. H2H is a *meetings list*, not a form window — the scope-purity rule binds **Rolling Windows**, not H2H. Degrades gracefully when two teams have few or no meetings. Cup meetings carry the cup caveats: whole-match Metrics (ET included) and occasional source sparsity.
_Avoid_: form (form is measured vs all opponents; H2H is vs one specific team).

### Scope & coverage

**Competition Type**:
The scope tag on every fact row, one of `club_league` | `club_cup` | `club_european` | `international`. "Last N games" is meaningless without it — a window is read within a scope **by default**; an explicit all-competitions window ("last N games played, regardless of competition") is a legitimate user choice, honest only to the extent competitive coverage is complete, and always labelled — never the silent default.

**v1 scope**:
The top four English tiers — Premier League, Championship, League One, League Two — `club_league` first, now extending into domestic cups (`club_cup`). **Team** data is confident across all four tiers (football-data.co.uk), league-only. **Player** data is confident for PL + Championship but **best-effort for League One / League Two** (Opta's lower-tier coverage was always thin, compounded by FBref's Jan-2026 removal) — verified at backfill and labelled in the UI as "covered competitions only", never implied complete. The first scope expansion added **FA Cup / EFL Cup player AND team data** for **Covered ties** (see below), including corners (see `docs/adr/0008`).

**Covered tie**:
A cup or European **Fixture** the project ingests: a tie with **at least one Premier League or Championship club in that season**. Decided season-aware from the **union** of a club's PL/Championship `team_match` and `fixtures` rows that season, so a club relegated out of the top two does not pull its cup ties into an untracked season. The union is deliberate: `team_match` alone sources the set from football-data.co.uk, so an outage there silently empties the covered set and rules real ties out of scope with no warning (observed 2026-08: with E0 unpublished, **no** Premier League club was covered for 2627, while ESPN-sourced fixtures held all 20). The two sources are compared each run and any divergence is logged — a source outage should be visible, not absorbed. For domestic cups this excludes the lower-/non-league-only early rounds; for European competitions (scoped 2026-07, ingestion pending) it excludes foreign-vs-foreign fixtures. The uncovered opponent — a League One side or a foreign club — is stored in full **for that Fixture** (canonical team row, team + player rows) as a bonus toward later coverage, but its other fixtures are not held: its form windows are partial and never implied complete. The filter loosens as coverage expands (League One/Two player data; other domestic leagues eventually).
_Avoid_: treating every tie in a competition's schedule as in scope.

**Promotion Play-offs**:
The end-of-season knockout deciding the last promotion place (Championship/League One/League Two): teams finishing 3rd–6th contest two-legged semi-finals (4 fixtures) then a single neutral-venue final. Modelled as its **own competition** ("Championship Play-offs", **Competition Type** `club_cup`), never as part of the regular season — a play-off leg shares the same home/away orientation as a league meeting, so keeping it under `club_league` collided on the Fixture natural key and contaminated league form (see `docs/adr/0004`). Player data only (football-data.co.uk does not cover play-offs). A "last N **league** games" window therefore excludes them by scope.
_Avoid_: treating a play-off game as a Championship (league) Fixture.

**Covered international competition** (fully ingested 2026-07-21 — finals, Nations League, and every qualifying campaign):
The boundary for the `international` scope — coverage is decided **by competition, not by nation**. The list: **World Cup, Euros, Copa América, AFCON, Asian Cup, Gold Cup**, their **qualifying campaigns** (all confederations for the World Cup), and the **UEFA Nations League**. World Cup qualifying is **one competition — "World Cup Qualifiers"** — regardless of confederation (the region is self-evident from the nations; the inter-confederation play-offs fold in too); the other qualifying campaigns are each their own competition (Euros Qualifying, AFCON Qualifying, Asian Cup Qualifying). `stage` carries the round (group stage, R16, …) on every fixture, finals and qualifiers alike. Every match in a covered competition is ingested wholesale — no player-derived "covered nation" filter, because English club squads span most footballing nations anyway and a whole-competition rule has no drifting, transfer-dependent boundary. **Friendlies and one-off exhibitions are excluded permanently**: sides don't take them seriously enough for their Metrics to be form signal (the same reasoning that excludes the Club World Cup on the club side). The purpose is **player-data continuity** — a tracked player's competitive caps exist in his **Appearance** history (all-competitions windows, segment-by-competition) — not fixture research; Metric sparsity on minor-confederation pages is accepted and surfaced as NULL (appearance/minutes/goals/cards continuity is itself the signal), never patched from lesser sources.
_Avoid_: "covered nation" (rejected concept — the filter saved little and drifted with every transfer window); mixing caps into a league window by default (all-competitions is an explicit, labelled choice).

**Dual-badged match**:
A match that belongs to two competitions *at the source* — the AFC plays one set of games as both World Cup qualification (2022) and Asian Cup qualification (2023) second round, and FBref lists it on both schedules under the same match id. Stored under **exactly one** competition: **the World Cup label wins** (decided 2026-07-09), enforced by chain order (WC Qualifiers ingests before Asian Cup Qualifying; the dedup guard — one Fixture per FBref match id — makes the second pass skip them). Consequence: Asian Cup Qualifying's fixture count is *expected* to sit below its schedule count; that is the decision working, not a silent drop.
_Avoid_: reading a fixtures-vs-schedule audit shortfall on a qualifying competition as data loss without checking for dual-badging first.

**Squad** (registered roster):
The set of players currently *registered* to a club, as listed on that club's FBref squad page — *membership*, not appearance history. A new signing who has not played yet is in the Squad; a player loaned out is not (he appears at his loan club). The roster source for this (Job C / FBref squad page, into the `squads` table) is **deferred past v1** (see `docs/adr/0006`); v1 uses **Recent squad** instead. Distinct from a **Player-Match**/**Appearance** (what a player *did*): the Squad decides *who* is shown, Appearances supply *the numbers*.
_Avoid_: lineup (a Squad is not a starting XI), roster as a synonym for the played-minutes set.

**Recent squad** (v1 membership, appearance-derived):
The set of players whose most-recent **Appearance** in covered data was for this club — "the squad as it last took the field." Derived at query time from each player's date-ordered **Player-Match** rows: the `team_id` of his `MAX(date)` appearance (the stored `current_team_id` is *not* trusted — it is last-written, not guaranteed chronological). No roster source. Accurate *during* a season; lags the summer transfer window — a sold player lingers in his old club's Recent squad until he debuts elsewhere in covered data (the accepted **"ghost"**), and a new signing appears only once he debuts. Always surfaced labelled as appearance-based, never implied to be the registered **Squad**. This is the v1 membership rule for **Squad form** (see `docs/adr/0006`).
_Avoid_: calling it the "current squad" without the appearance-based caveat.

**Squad form** (not lineup):
Recent Summary Metrics for each player in a team's **Recent squad** (v1) — membership from recent **Appearances**, the form numbers from each player's Appearances. One reusable panel, shown on two surfaces: the **Fixture view** (both teams side by side, its primary home) and the **Team hub** (one team, a jump-off into its players). Never an auto-predicted XI. Filtering to confirmed starters is a manual user step once the official lineup is released. When the roster source lands post-v1, membership switches from **Recent squad** to **Squad** with no change to the form numbers.

### Out of scope

**FIFA Club World Cup**:
Excluded from `club_european` deliberately (2026-07): squads demonstrably don't take it seriously and the opposition is much weaker, so its rows would be form noise inside a covered club's Rolling Windows — the exclusion is about signal quality, not fetch cost. Same reasoning excludes international friendlies.

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
