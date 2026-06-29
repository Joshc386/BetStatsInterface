"""Squad-form query layer — Recent-squad membership + raw appearance rows.

The Squad-form panel shows a club's **Recent squad** (players whose most-recent
Appearance was for that club, derived from `player_match` — NOT the stored
`current_team_id`, which is last-written, not chronological) with each member's
raw rows for client-side aggregation (see docs/adr/0006). Mirrors app/fixtures.py:
we return raw rows and let the client compute the Summary Metrics.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.models.facts import PlayerMatch
from app.models.reference import Competition, Player, Team

# Every per-appearance column the client needs to render a row and aggregate any
# player Metric (mirrors PLAYER_METRICS in app/stats.py).
_METRIC_COLS = (
    PlayerMatch.minutes,
    PlayerMatch.goals,
    PlayerMatch.assists,
    PlayerMatch.shots,
    PlayerMatch.sot,
    PlayerMatch.tackles,
    PlayerMatch.fouls_drawn,
    PlayerMatch.fouls_committed,
    PlayerMatch.yellows,
    PlayerMatch.reds,
    PlayerMatch.second_yellows,
    PlayerMatch.carded,
)


def recent_squad(session: Session, *, team_id: int) -> list:
    """The club's Recent squad: players whose most-recent appearance (any
    competition) was for this club. Each member carries his `last_seen` — the
    date of that most-recent appearance, which doubles as the ghost-detector.
    """
    last_app = (
        select(
            PlayerMatch.player_id,
            PlayerMatch.team_id.label("last_team_id"),
            PlayerMatch.date.label("last_seen"),
        )
        .distinct(PlayerMatch.player_id)
        .order_by(
            PlayerMatch.player_id,
            PlayerMatch.date.desc(),
            PlayerMatch.fixture_id.desc(),  # deterministic same-day tiebreak
        )
        .subquery()
    )
    q = (
        select(
            last_app.c.player_id,
            Player.canonical_name.label("player"),
            last_app.c.last_seen,
        )
        .join(Player, Player.id == last_app.c.player_id)
        .where(last_app.c.last_team_id == team_id)
        .order_by(last_app.c.last_seen.desc(), Player.canonical_name)
    )
    return list(session.execute(q).all())


def _member_rows_at_club(
    session: Session, team_id: int, member_ids: list[int], cap: int
) -> list:
    """Each member's last `cap` appearances FOR THIS CLUB (all scopes),
    most-recent-first — the raw rows the client aggregates."""
    if not member_ids:
        return []
    opp = aliased(Team)
    rn = func.row_number().over(
        partition_by=PlayerMatch.player_id,
        order_by=PlayerMatch.date.desc(),
    ).label("rn")
    base = (
        select(
            PlayerMatch.player_id,
            Player.canonical_name.label("player"),
            PlayerMatch.date,
            PlayerMatch.season,
            PlayerMatch.competition_id,
            Competition.name.label("competition"),
            PlayerMatch.competition_type,
            PlayerMatch.opponent_id,
            opp.canonical_name.label("opponent"),
            PlayerMatch.is_home,
            *_METRIC_COLS,
            rn,
        )
        .join(Player, Player.id == PlayerMatch.player_id)
        .join(opp, opp.id == PlayerMatch.opponent_id)
        .join(Competition, Competition.id == PlayerMatch.competition_id)
        .where(
            PlayerMatch.team_id == team_id,
            PlayerMatch.player_id.in_(member_ids),
        )
        .subquery()
    )
    q = (
        select(base)
        .where(base.c.rn <= cap)
        .order_by(base.c.player_id, base.c.date.desc())
    )
    return list(session.execute(q).all())


def squad_form(session: Session, *, team_id: int, cap: int = 30) -> dict:
    """Raw rows for the Squad-form panel: the club's Recent squad plus each
    member's last `cap` appearances at the club (the client aggregates, ADR 0006)."""
    members = recent_squad(session, team_id=team_id)
    member_ids = [m.player_id for m in members]
    return {
        "members": members,
        "rows": _member_rows_at_club(session, team_id, member_ids, cap),
    }
