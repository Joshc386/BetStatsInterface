"""Entity-generic Summary Metric computation — the rolling-window heart.

Works identically over team_match and player_match: pick a Metric, a Rolling Window
(last-N games or season(s)) within a Competition Type scope, and get the headline
(total / average / hit-rate vs a Threshold) plus the per-game Breakdown. Computed at
query time; nothing rolling is stored.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models.facts import PlayerMatch, TeamMatch
from app.models.reference import Player, Team

# metric name -> (column attribute, kind) where kind is "count" or "bool"
TEAM_METRICS: dict[str, tuple[str, str]] = {
    "goals_for": ("gf", "count"),
    "goals_against": ("ga", "count"),
    "shots": ("shots", "count"),
    "shots_on_target": ("sot", "count"),
    "shots_conceded": ("shots_conceded", "count"),
    "sot_conceded": ("sot_conceded", "count"),
    "fouls": ("fouls", "count"),
    "corners": ("corners", "count"),
    "yellows": ("yellows", "count"),
    "reds": ("reds", "count"),
    "total_goals": ("total_goals", "count"),
    "clean_sheet": ("clean_sheet", "bool"),
    "btts": ("btts", "bool"),
}

PLAYER_METRICS: dict[str, tuple[str, str]] = {
    "shots": ("shots", "count"),
    "shots_on_target": ("sot", "count"),
    "tackles": ("tackles", "count"),
    "fouls_drawn": ("fouls_drawn", "count"),
    "fouls_committed": ("fouls_committed", "count"),
    "yellows": ("yellows", "count"),
    "reds": ("reds", "count"),
    "minutes": ("minutes", "count"),
    "carded": ("carded", "bool"),
}


def registry(entity: str) -> dict[str, tuple[str, str]]:
    return TEAM_METRICS if entity == "team" else PLAYER_METRICS


def entity_summary(
    session: Session,
    *,
    entity: str,
    entity_id: int,
    metric: str,
    n: int = 10,
    scope: str = "club_league",
    seasons: list[str] | None = None,
    threshold: float | None = None,
    direction: str = "over",
    window_mode: str = "display",  # "display" (inclusive) | "going_in" (excludes latest)
) -> dict:
    table = TeamMatch if entity == "team" else PlayerMatch
    id_col = TeamMatch.team_id if entity == "team" else PlayerMatch.player_id
    name_model = Team if entity == "team" else Player

    attr, kind = registry(entity)[metric]
    opp = aliased(Team)
    cols = [
        table.date,
        table.is_home,
        opp.canonical_name.label("opponent"),
        getattr(table, attr).label("value"),
    ]
    if entity == "player":
        cols.append(table.minutes.label("minutes"))

    q = (
        select(*cols)
        .join(opp, opp.id == table.opponent_id)
        .where(id_col == entity_id, table.competition_type == scope)
        .order_by(table.date.desc())
    )
    if seasons:
        q = q.where(table.season.in_(seasons))
        window = f"seasons={seasons} scope={scope}"
    else:
        q = q.offset(1 if window_mode == "going_in" else 0).limit(n)
        window = f"last {n} ({window_mode}) scope={scope}"

    rows = list(session.execute(q).all())[::-1]  # chronological
    games = len(rows)
    values = [r.value for r in rows if r.value is not None]

    if kind == "bool":
        total: float | None = sum(1 for v in values if v)
        average = (total / len(values)) if values else None
    else:
        total = sum(values) if values else 0
        average = (total / len(values)) if values else None

    hit_rate = None
    if threshold is not None and values:
        if kind == "bool":
            want = direction == "over"
            hits = sum(1 for v in values if bool(v) is want)
        elif direction == "under":
            hits = sum(1 for v in values if v <= threshold)
        else:
            hits = sum(1 for v in values if v >= threshold)
        hit_rate = {
            "threshold": threshold,
            "direction": direction,
            "hits": hits,
            "n": len(values),
            "pct": round(100 * hits / len(values), 1),
        }

    per_appearance = per_90 = minutes_total = None
    if entity == "player":
        minutes_total = sum(r.minutes for r in rows if r.minutes is not None)
        per_appearance = (total / games) if games else None
        per_90 = (total / minutes_total * 90) if minutes_total else None

    name = session.scalar(select(name_model.canonical_name).where(name_model.id == entity_id))

    return {
        "entity": entity,
        "entity_id": entity_id,
        "entity_name": name,
        "metric": metric,
        "scope": scope,
        "window": window,
        "games": games,
        "total": total,
        "average": average,
        "per_appearance": per_appearance,
        "per_90": per_90,
        "minutes_total": minutes_total,
        "hit_rate": hit_rate,
        "breakdown": [
            {
                "date": r.date,
                "opponent": r.opponent,
                "is_home": r.is_home,
                "value": r.value,
                "minutes": getattr(r, "minutes", None),
            }
            for r in rows
        ],
    }
