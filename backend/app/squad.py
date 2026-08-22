"""Squad-form query layer — Squad membership + raw appearance rows.

The panel shows a club's **Squad** (the ESPN roster, refreshed daily into
`squads` by ingestion.squads) with each member's raw rows for client-side
aggregation. Membership moved here from the appearance-derived **Recent squad**
in ADR 0013, because that rule could not clear a departed player whose next club
is outside covered data — Wolves still listed Diego Costa and Patrick Cutrone.

Two rules keep it honest:

* **Squad ∪ anyone who appeared for the club in the last 30 days.** A safety net
  against an unreconciled name, not a widening: a player who turned out last
  week is self-evidently still there whatever the roster spells him. Short on
  purpose — a season-long union would keep a January departure until August.
* **No roster, no filter.** A club with no `espn_id` (a non-league cup opponent,
  a foreign European club) has no Squad at all, and falls back to Recent squad
  rather than showing an empty panel. The response says which was used so the
  panel can label itself honestly.

Mirrors app/fixtures.py: raw rows out, the client computes the Summary Metrics.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.models.facts import PlayerMatch, Squad
from app.models.reference import Competition, Player, Team

# How far back the union reaches. Every false negative measured when the roster
# source was spiked had played within 8 days, so this is generous for its actual
# job while still dropping a mid-season departure within the month.
UNION_DAYS = 30

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


def _last_seen_at_club(session: Session, team_id: int) -> dict[int, dt.datetime]:
    """Each player's most-recent appearance FOR THIS CLUB.

    Deliberately not his most-recent appearance anywhere: a player signed from
    another tracked club last played for his OLD club, and showing that date
    here would read as though he had featured for this one.
    """
    rows = session.execute(
        select(PlayerMatch.player_id, func.max(PlayerMatch.date))
        .where(PlayerMatch.team_id == team_id)
        .group_by(PlayerMatch.player_id)
    ).all()
    return {pid: last for pid, last in rows}


def squad_members(
    session: Session, *, team_id: int, now: dt.datetime | None = None
) -> tuple[list[dict], str]:
    """The panel's membership, and which rule produced it.

    Returns ``(members, source)`` where source is ``"squad"`` (ESPN roster ∪ the
    last 30 days) or ``"recent"`` (the ADR 0006 fallback, for a club with no
    roster). A member's ``last_seen`` is None when we hold no appearance for him
    at this club — a new signing is in the Squad and unknown to us, which is
    different from not being in the squad at all.
    """
    roster_ids = set(
        session.scalars(
            select(Squad.player_id).where(
                Squad.team_id == team_id, Squad.active.is_(True)
            )
        )
    )
    if not roster_ids:
        members = [
            {"player_id": m.player_id, "player": m.player, "last_seen": m.last_seen}
            for m in recent_squad(session, team_id=team_id)
        ]
        return members, "recent"

    now = now or dt.datetime.now(dt.timezone.utc)
    recent_ids = set(
        session.scalars(
            select(PlayerMatch.player_id)
            .where(
                PlayerMatch.team_id == team_id,
                PlayerMatch.date >= now - dt.timedelta(days=UNION_DAYS),
            )
            .distinct()
        )
    )
    last_seen = _last_seen_at_club(session, team_id)
    names = {
        pid: name
        for pid, name in session.execute(
            select(Player.id, Player.canonical_name).where(
                Player.id.in_(roster_ids | recent_ids)
            )
        ).all()
    }
    members = [
        {"player_id": pid, "player": names[pid], "last_seen": last_seen.get(pid)}
        for pid in roster_ids | recent_ids
        if pid in names
    ]
    # most recently seen first; the never-seen (new signings) fall to the bottom
    members.sort(
        key=lambda m: (m["last_seen"] is not None, m["last_seen"], m["player"])
        if m["last_seen"] is not None
        else (False, dt.datetime.min.replace(tzinfo=dt.timezone.utc), m["player"]),
        reverse=True,
    )
    return members, "squad"


def squad_form(session: Session, *, team_id: int, cap: int = 30) -> dict:
    """Raw rows for the Squad-form panel: the club's Squad plus each member's
    last `cap` appearances at the club (the client aggregates, ADR 0006/0013).

    A member with no appearances at the club contributes no rows — the panel
    renders him with no figure rather than omitting him.
    """
    members, source = squad_members(session, team_id=team_id)
    member_ids = [m["player_id"] for m in members]
    return {
        "members": members,
        "membership": source,
        "rows": _member_rows_at_club(session, team_id, member_ids, cap),
    }
