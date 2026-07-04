"""Computed league table (docs/adr/0010).

Standings are an aggregation over team_match — never fetched. Points
deductions come from `points_adjustments` (seeded from ESPN, see
ingestion/points_adjustments.py) and apply in full even on `as_of` views:
ruling dates aren't stored, so a mid-season historical table shows the
season's final adjustment. Ordering is the English league tie-break:
points, goal difference, goals for, then name.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.facts import PointsAdjustment, TeamMatch
from app.models.reference import Team


def league_seasons(session: Session, competition_id: int) -> list[str]:
    """Seasons with team_match rows for this competition, oldest first."""
    return sorted(
        session.scalars(
            select(TeamMatch.season)
            .where(TeamMatch.competition_id == competition_id)
            .distinct()
        )
    )


def league_table(
    session: Session,
    *,
    competition_id: int,
    season: str,
    as_of: dt.date | None = None,
) -> list[dict]:
    """One league-season's standings, optionally as of a date (inclusive)."""
    conditions = [
        TeamMatch.competition_id == competition_id,
        TeamMatch.season == season,
    ]
    if as_of is not None:
        conditions.append(TeamMatch.date < as_of + dt.timedelta(days=1))

    won = func.sum(case((TeamMatch.result == "W", 1), else_=0))
    drawn = func.sum(case((TeamMatch.result == "D", 1), else_=0))
    lost = func.sum(case((TeamMatch.result == "L", 1), else_=0))
    rows = session.execute(
        select(
            TeamMatch.team_id,
            Team.canonical_name,
            func.count().label("played"),
            won.label("won"),
            drawn.label("drawn"),
            lost.label("lost"),
            func.sum(TeamMatch.gf).label("gf"),
            func.sum(TeamMatch.ga).label("ga"),
        )
        .join(Team, Team.id == TeamMatch.team_id)
        .where(*conditions)
        .group_by(TeamMatch.team_id, Team.canonical_name)
    ).all()

    adjustments = {
        team_id: (points, note)
        for team_id, points, note in session.execute(
            select(
                PointsAdjustment.team_id,
                PointsAdjustment.points,
                PointsAdjustment.note,
            ).where(
                PointsAdjustment.competition_id == competition_id,
                PointsAdjustment.season == season,
            )
        )
    }

    table = []
    for team_id, name, played, w, d, l, gf, ga in rows:
        adj, note = adjustments.get(team_id, (0, None))
        table.append(
            {
                "team_id": team_id,
                "team_name": name,
                "played": played,
                "won": w,
                "drawn": d,
                "lost": l,
                "gf": gf,
                "ga": ga,
                "gd": gf - ga,
                "points": 3 * w + d + adj,
                "adjustment": adj,
                "adjustment_note": note,
            }
        )
    table.sort(
        key=lambda r: (-r["points"], -r["gd"], -r["gf"], r["team_name"])
    )
    for position, row in enumerate(table, start=1):
        row["position"] = position
    return table
