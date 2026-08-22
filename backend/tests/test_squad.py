"""Squad-form membership — Squad ∪ 30-day, with a Recent-squad fallback.

Membership moved from appearances to the ESPN roster (ADR 0013). These pin the
three behaviours that decide whether the panel can be trusted: ghosts are gone,
a player who is genuinely there is never hidden, and a club with no roster still
gets a panel.

Real data, mutated inside rolled-back sessions — the union and the fallback are
both about the interaction between two tables, which a synthetic fixture would
not exercise honestly.
"""

import datetime as dt

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.facts import PlayerMatch, Squad
from app.models.reference import Player, Team
from app.squad import UNION_DAYS, squad_form, squad_members


def _wolves(session) -> Team:
    return session.scalar(select(Team).where(Team.canonical_name == "Wolves"))


def test_membership_comes_from_the_roster_when_there_is_one():
    with SessionLocal() as s:
        members, source = squad_members(s, team_id=_wolves(s).id)
        assert source == "squad"
        assert 15 < len(members) < 40  # a squad, not an appearance archive


def test_multi_year_ghosts_are_gone():
    """The complaint that started this: players who last turned out years ago."""
    with SessionLocal() as s:
        team = _wolves(s)
        members, _ = squad_members(s, team_id=team.id)
        names = {m["player"] for m in members}
        assert "Patrick Cutrone" not in names   # last seen 2021-01-22
        assert "Oskar Buur" not in names        # last seen 2020-09-17


def test_a_recent_player_missing_from_the_roster_is_still_kept():
    """The safety net. If a name fails to reconcile, the player must NOT vanish —
    he played last week, so he is self-evidently still at the club."""
    with SessionLocal() as s:
        team = _wolves(s)
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=UNION_DAYS)
        recent_id = s.scalar(
            select(PlayerMatch.player_id)
            .where(PlayerMatch.team_id == team.id, PlayerMatch.date >= cutoff)
            .limit(1)
        )
        if recent_id is None:
            pytest.skip("no appearance inside the union window to test with")

        # simulate the reconciliation failure: drop him from the Squad
        s.execute(
            Squad.__table__.delete().where(
                Squad.team_id == team.id, Squad.player_id == recent_id
            )
        )
        s.flush()

        members, _ = squad_members(s, team_id=team.id)

        assert recent_id in {m["player_id"] for m in members}
        s.rollback()


def test_a_club_with_no_roster_falls_back_to_recent_squad():
    """Non-league cup opponents and foreign clubs have no espn_id, so no Squad.
    They must still get a panel, honestly labelled."""
    with SessionLocal() as s:
        rostered = set(s.scalars(select(Squad.team_id).distinct()))
        team_id = s.scalar(
            select(PlayerMatch.team_id)
            .where(PlayerMatch.team_id.notin_(rostered))
            .limit(1)
        )
        if team_id is None:
            pytest.skip("every club with appearances has a roster")

        members, source = squad_members(s, team_id=team_id)

        assert source == "recent"
        assert members  # not an empty panel


def test_a_roster_member_we_hold_nothing_on_is_included_with_no_last_seen():
    """A new signing is IN the squad and we know nothing about him. 'In the
    squad, nothing known' is different information from 'not in the squad'."""
    with SessionLocal() as s:
        team = _wolves(s)
        members, _ = squad_members(s, team_id=team.id)
        no_data = [m for m in members if m["last_seen"] is None]
        assert no_data, "expected at least one roster member with no appearances"
        for m in no_data:
            assert m["player"]  # still named


def test_squad_form_returns_no_rows_for_a_no_data_member():
    """The contract change ADR 0006 never allowed: a member may carry zero rows."""
    with SessionLocal() as s:
        team = _wolves(s)
        data = squad_form(s, team_id=team.id)
        with_rows = {r.player_id for r in data["rows"]}
        no_data = [m for m in data["members"] if m["last_seen"] is None]
        for m in no_data:
            assert m["player_id"] not in with_rows


def test_last_seen_is_the_players_date_at_THIS_club():
    """Not his most-recent appearance anywhere. A player signed from another
    tracked club last played for his OLD club, and showing that date in this
    club's panel would read as though he had featured here."""
    with SessionLocal() as s:
        team = _wolves(s)
        members, _ = squad_members(s, team_id=team.id)
        for m in members:
            if m["last_seen"] is None:
                continue
            here = s.scalar(
                select(func.max(PlayerMatch.date)).where(
                    PlayerMatch.team_id == team.id,
                    PlayerMatch.player_id == m["player_id"],
                )
            )
            assert here is not None and here == m["last_seen"]
