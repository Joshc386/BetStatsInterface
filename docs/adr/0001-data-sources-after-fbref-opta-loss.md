# Data sources after FBref's Opta feed loss

**Status:** accepted

In the week of 22 January 2026, StatsPerform/Opta terminated FBref's data feed, and FBref removed all **modeled** advanced stats (xG, npxG, xAG, percentiles, advanced historical archives). FBref **retained** its event-count match stats — shots, shots on target, tackles, fouls committed/drawn, cards, minutes, goals, assists — verified directly against a recent Premier League match page. This invalidated the original spec's assumption that FBref supplies xG, and its non-negotiable that *all* player data comes from FBref unconditionally.

## Decision

Source data per type, not per provider:

- **Player per-match event stats** (Sh, SoT, Tkl, Fls, Fld, cards, minutes, goals, assists), all scopes → **FBref**. Still the richest free per-player match source.
- **Team per-match stats, league scope** → **football-data.co.uk** (no rate limit, deep history, cheap incremental). League-only.
- **Team per-match stats, cup / European / international scope** → **FBref** (football-data.co.uk is league-only).
- **Team xG, per-match, all scopes** → **FotMob** (via `soccerdata`'s `FotMob.read_team_match_stats`). This replaces FBref's lost xG.
- **Player xG, per-match** → **none in v1**. `player_match.xg` stays NULL. soccerdata's FotMob reader exposes no player-match method, and FBref no longer has it.

`xG` columns remain **nullable** everywhere — FotMob coverage is not universal, and player xG is absent in v1.

## Considered options

- **WhoScored for xG** — rejected. Its site exposes only season-aggregate xG, which cannot be decomposed into the per-match rows our fact-table model requires. Its per-match event stream (`read_events`) could yield it, but that reader is Selenium-based, anti-bot-fragile, and ToS-gray — it contradicts the project's "boring, reliable pipeline" mandate.
- **Understat for player xG** — rejected as a v1 dependency. Shooting-only (no tackles/fouls/cards) and top-5 leagues only (no Championship). Viable as a later optional top-5 shooting module, not a foundation.
- **Drop xG entirely** — rejected. Team xG is cheaply available from FotMob and is a high-value modern Metric.

## Consequences

- Reconciliation is now **three-way** (FBref + football-data.co.uk + FotMob). `teams` gains a `fotmob_id` column; the name→canonical-id mapping step resolves FotMob source names too.
- A **player-xG ingestion path is kept open for later** — a custom FotMob player-match ingestion job (respectful, cached, rate-limited, ingestion-layer only — never request-time). Deferred, not abandoned.
- All downstream sources (FotMob, Understat, WhoScored, Sofascore) ultimately rely on Opta-derived data and could face the same feed pressure FBref did. Source fragility is now a standing operational risk, not a one-off.
