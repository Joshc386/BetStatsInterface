"""Fixture-view query layer — raw team_match rows for client-side aggregation.

The Fixture view compares two teams (Team form + Head-to-Head) across every team
Metric. Rather than aggregate server-side per metric, we return the raw rows and
let the client compute hit-rates / averages / venue filters with no further
round-trips (see docs/adr/0005). `entity_summary` is untouched and still powers
the single-metric Entity view.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models.facts import TeamMatch
from app.models.reference import Competition, Team

# Every column the client needs to render a fixture row and aggregate any metric.
_ROW_COLS = (
    TeamMatch.fixture_id,
    TeamMatch.team_id,
    TeamMatch.date,
    TeamMatch.season,
    TeamMatch.competition_id,
    TeamMatch.is_home,
    TeamMatch.opponent_id,
    TeamMatch.gf,
    TeamMatch.ga,
    TeamMatch.shots,
    TeamMatch.sot,
    TeamMatch.shots_conceded,
    TeamMatch.sot_conceded,
    TeamMatch.corners,
    TeamMatch.fouls,
    TeamMatch.yellows,
    TeamMatch.reds,
    TeamMatch.total_goals,
    TeamMatch.btts,
    TeamMatch.clean_sheet,
    TeamMatch.result,
)


def _rows_query():
    """Base SELECT projecting a display row (metrics + opponent/competition
    labels). Callers add the WHERE / ORDER / LIMIT they need."""
    opp = aliased(Team)
    return (
        select(
            *_ROW_COLS,
            opp.canonical_name.label("opponent"),
            Competition.name.label("competition"),
        )
        .join(opp, opp.id == TeamMatch.opponent_id)
        .join(Competition, Competition.id == TeamMatch.competition_id)
    )


def _team_rows(session: Session, team_id: int, n: int):
    """A team's last-n league games, most-recent-first."""
    q = (
        _rows_query()
        .where(
            TeamMatch.team_id == team_id,
            TeamMatch.competition_type == "club_league",
        )
        .order_by(TeamMatch.date.desc())
        .limit(n)
    )
    return list(session.execute(q).all())


def _h2h_rows(session: Session, home_id: int, away_id: int, n: int):
    """Both sides of the last-n league meetings between the two teams.

    Both perspectives are returned because the opponent's corners/fouls/cards
    are not derivable from a single team's row (only shots/sot have a `conceded`
    column); the client pairs the two rows per fixture.
    """
    meeting_ids = session.execute(
        select(TeamMatch.fixture_id)
        .where(
            TeamMatch.team_id == home_id,
            TeamMatch.opponent_id == away_id,
            TeamMatch.competition_type == "club_league",
        )
        .order_by(TeamMatch.date.desc())
        .limit(n)
    ).scalars().all()
    if not meeting_ids:
        return []
    q = (
        _rows_query()
        .where(TeamMatch.fixture_id.in_(meeting_ids))
        .order_by(TeamMatch.date.desc())
    )
    return list(session.execute(q).all())


def fixture_detail(session: Session, *, fixture_id: int):
    """Both teams' full team_match rows for one fixture (the drill-down)."""
    q = (
        _rows_query()
        .where(TeamMatch.fixture_id == fixture_id)
        .order_by(TeamMatch.is_home.desc())  # home side first
    )
    return list(session.execute(q).all())


def fixture_comparison(
    session: Session, *, home_id: int, away_id: int, n: int = 10
) -> dict:
    """Raw rows for the Fixture view: each team's last-n league games plus both
    sides of their last-n league meetings."""
    return {
        "home": _team_rows(session, home_id, n),
        "away": _team_rows(session, away_id, n),
        "h2h": _h2h_rows(session, home_id, away_id, n),
    }
