"""Behaviour tests for the Squad-form query layer (app/squad.py).

Function-level, against the real DB (read-only), mirroring test_fixtures.py.
The Squad-form panel shows a club's **Recent squad** — players whose most-recent
Appearance was for that club — with each member's raw rows for client-side
aggregation (see docs/adr/0006). Anchor: Adam Armstrong (id 7634), who has played
for multiple clubs, so his MAX(date) club is the only squad he belongs to.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db import SessionLocal
from app.main import team_squad_form
from app.models.facts import PlayerMatch
from app.models.reference import Player
from app.squad import recent_squad, squad_form
from app.stats import entity_summary

ARMSTRONG = 7634


def _last_club(session, pid: int) -> int:
    """The team of a player's most-recent appearance — the membership oracle."""
    return session.scalar(
        select(PlayerMatch.team_id)
        .where(PlayerMatch.player_id == pid)
        .order_by(PlayerMatch.date.desc())
        .limit(1)
    )


def _earlier_clubs(session, pid: int, last: int) -> list[int]:
    return [
        t for t in session.execute(
            select(PlayerMatch.team_id).where(PlayerMatch.player_id == pid).distinct()
        ).scalars().all()
        if t != last
    ]


def test_membership_is_the_max_date_club_only():
    """A multi-club player belongs to exactly the squad of his most-recent
    appearance — and to none of his earlier clubs (he transferred away)."""
    s = SessionLocal()
    try:
        assert s.get(Player, ARMSTRONG), "expected Adam Armstrong in the data"
        last = _last_club(s, ARMSTRONG)
        earlier = _earlier_clubs(s, ARMSTRONG, last)
        assert earlier, "anchor player must have at least one earlier club"

        in_last = {m.player_id for m in recent_squad(s, team_id=last)}
        assert ARMSTRONG in in_last  # belongs to his current club's Recent squad

        for club in earlier:
            in_earlier = {m.player_id for m in recent_squad(s, team_id=club)}
            assert ARMSTRONG not in in_earlier  # not lingering in a former club
    finally:
        s.close()


def test_member_last_seen_is_their_global_latest_appearance():
    """Membership = "his last game *anywhere* was here", so each member's
    last_seen equals his global MAX(date). This is the invariant that makes the
    accepted "ghost" correct (a stale member lingers only because his global
    last game was at this club) and excludes anyone who later played elsewhere."""
    s = SessionLocal()
    try:
        last = _last_club(s, ARMSTRONG)
        members = recent_squad(s, team_id=last)
        assert members, "expected a non-empty Recent squad"

        for m in members:
            global_max = s.scalar(
                select(PlayerMatch.date)
                .where(PlayerMatch.player_id == m.player_id)
                .order_by(PlayerMatch.date.desc())
                .limit(1)
            )
            assert m.last_seen == global_max
    finally:
        s.close()


def test_rows_are_each_members_last_cap_appearances_at_the_club():
    """The raw rows are every member's last `cap` appearances FOR THIS CLUB
    (all scopes), most-recent-first — the client windows/aggregates them."""
    s = SessionLocal()
    try:
        cap = 5
        last = _last_club(s, ARMSTRONG)
        res = squad_form(s, team_id=last, cap=cap)
        members = {m.player_id for m in res["members"]}
        assert members, "expected a non-empty Recent squad"

        # every row belongs to a member, and no member exceeds the cap
        by_player: dict[int, list] = {}
        for r in res["rows"]:
            by_player.setdefault(r.player_id, []).append(r)
        assert set(by_player) <= members
        assert all(len(rs) <= cap for rs in by_player.values())

        # spot-check the anchor against a direct club-filtered oracle
        direct = s.execute(
            select(PlayerMatch.date)
            .where(PlayerMatch.player_id == ARMSTRONG, PlayerMatch.team_id == last)
            .order_by(PlayerMatch.date.desc())
            .limit(cap)
        ).scalars().all()
        got = [r.date for r in by_player[ARMSTRONG]]
        assert got == list(direct)  # same rows, same most-recent-first order, at this club
    finally:
        s.close()


def test_client_aggregation_of_rows_reconciles_to_entity_summary():
    """The raw-rows contract (ADR 0006/0005): client-side aggregation over the
    returned rows, filtered to a scope, reproduces entity_summary's figure for
    the same (player, club, metric, scope). entity_summary is the oracle.

    cap is large here so both sides cover the player's full set at the club —
    sidestepping the cap-vs-scope interaction (cup games are sparse in practice)."""
    s = SessionLocal()
    try:
        last = _last_club(s, ARMSTRONG)
        res = squad_form(s, team_id=last, cap=1000)

        league = [
            r for r in res["rows"]
            if r.player_id == ARMSTRONG and r.competition_type == "club_league"
        ]
        rows_total = sum(r.sot for r in league if r.sot is not None)
        rows_apps = len(league)

        oracle = entity_summary(
            s, entity="player", entity_id=ARMSTRONG, metric="shots_on_target",
            n=1000, scope="club_league", team_id=last,
        )
        assert rows_total == oracle["total"]
        assert rows_apps == oracle["games"]
    finally:
        s.close()


def test_endpoint_returns_squad_form_and_404s_unknown_team():
    s = SessionLocal()
    try:
        last = _last_club(s, ARMSTRONG)

        body = team_squad_form(team_id=last, cap=30, session=s)
        assert body.team_id == last
        assert body.team_name
        assert body.members and body.rows
        assert {"player_id", "player", "last_seen"} <= body.members[0].model_dump().keys()
        assert {"player_id", "competition_type", "sot", "minutes"} <= body.rows[0].model_dump().keys()

        with pytest.raises(HTTPException) as exc:
            team_squad_form(team_id=999999, cap=30, session=s)
        assert exc.value.status_code == 404
    finally:
        s.close()
