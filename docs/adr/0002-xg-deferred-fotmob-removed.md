# xG deferred — FotMob removed from soccerdata 1.9.0

**Status:** accepted — supersedes the xG-source decision in ADR 0001.

ADR 0001 routed team xG through FotMob (`soccerdata` `FotMob.read_team_match_stats`). When the project's venv was created it pulled **`soccerdata` 1.9.0, which removed the FotMob reader entirely** (the 1.8.x line still had it). Checking the remaining 1.9.0 readers: Sofascore exposes no per-match stats, and Understat — the only xG-capable reader — covers **top-5 leagues only** (Premier League yes; Championship / League One / League Two no). FBref no longer carries xG (ADR 0001). So there is no free xG source for Championship or the lower English tiers.

## Decision

**xG is deferred out of v1.** The `team_match.xg` and `player_match.xg` columns stay (nullable) but no xG is ingested in v1. The event-count core (shots, SoT, tackles, fouls, cards, minutes, goals, assists) — which works across all four tiers — is the v1 product. `soccerdata` is pinned to `==1.9.0` for reproducibility.

The documented future path: **Understat** team + player xG for the **Premier League only** (`read_team_match_stats` / `read_player_match_stats`), ingestion-layer only. Revisited post-v1.

## Consequences

- v1 surfaces no xG anywhere; the UI must not imply it exists.
- `teams.fotmob_id` / `players.fotmob_id` columns become dormant (FotMob is no longer a source) but are harmless and left in place.
- Confirms the standing source-fragility risk from ADR 0001: a soccerdata point-release silently removed a planned source. Pinning the version is the mitigation.
