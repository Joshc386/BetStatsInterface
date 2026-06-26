"""Behaviour tests for the Fixture-view query layer (app/fixtures.py).

Function-level, against the real DB (rolled back where it writes — these only
read). Mirrors the test_stats.py style: pick real teams with data, assert the
contract against a direct query. See docs/adr/0005.
"""

from sqlalchemy import select

from app.db import SessionLocal
from app.models.facts import TeamMatch
from app.fixtures import fixture_comparison, fixture_detail


def _a_pair_with_meetings(session) -> tuple[int, int]:
    """A (home, away) pair guaranteed to have at least one league meeting."""
    home = session.scalar(
        select(TeamMatch.team_id)
        .where(TeamMatch.competition_type == "club_league")
        .limit(1)
    )
    away = session.scalar(
        select(TeamMatch.opponent_id)
        .where(TeamMatch.team_id == home)
        .limit(1)
    )
    return home, away


def test_home_block_is_that_teams_last_n_league_games():
    s = SessionLocal()
    try:
        home, away = _a_pair_with_meetings(s)
        res = fixture_comparison(s, home_id=home, away_id=away, n=5)

        direct = s.execute(
            select(TeamMatch.fixture_id)
            .where(
                TeamMatch.team_id == home,
                TeamMatch.competition_type == "club_league",
            )
            .order_by(TeamMatch.date.desc())
            .limit(5)
        ).all()
        direct_ids = [fid for (fid,) in direct]

        got_ids = [r.fixture_id for r in res["home"]]
        assert got_ids == direct_ids          # same rows, same most-recent-first order
        assert len(got_ids) <= 5
    finally:
        s.close()


def test_away_block_is_the_other_teams_last_n_league_games():
    s = SessionLocal()
    try:
        home, away = _a_pair_with_meetings(s)
        res = fixture_comparison(s, home_id=home, away_id=away, n=5)

        direct = s.execute(
            select(TeamMatch.fixture_id)
            .where(
                TeamMatch.team_id == away,
                TeamMatch.competition_type == "club_league",
            )
            .order_by(TeamMatch.date.desc())
            .limit(5)
        ).all()
        assert [r.fixture_id for r in res["away"]] == [fid for (fid,) in direct]
    finally:
        s.close()


def test_h2h_block_is_both_sides_of_the_last_n_meetings():
    s = SessionLocal()
    try:
        home, away = _a_pair_with_meetings(s)
        res = fixture_comparison(s, home_id=home, away_id=away, n=5)

        meeting_ids = [
            fid for (fid,) in s.execute(
                select(TeamMatch.fixture_id)
                .where(
                    TeamMatch.team_id == home,
                    TeamMatch.opponent_id == away,
                    TeamMatch.competition_type == "club_league",
                )
                .order_by(TeamMatch.date.desc())
                .limit(5)
            ).all()
        ]
        assert meeting_ids, "test pair must have at least one meeting"

        by_fixture: dict[int, set[int]] = {}
        for r in res["h2h"]:
            by_fixture.setdefault(r.fixture_id, set()).add(r.team_id)

        # exactly the last-n meetings, both sides present for each
        assert set(by_fixture) == set(meeting_ids)
        for sides in by_fixture.values():
            assert sides == {home, away}
    finally:
        s.close()


def _two_teams_that_never_met(session) -> tuple[int, int]:
    home = session.scalar(
        select(TeamMatch.team_id)
        .where(TeamMatch.competition_type == "club_league")
        .limit(1)
    )
    opponents = session.execute(
        select(TeamMatch.opponent_id).where(TeamMatch.team_id == home).distinct()
    ).scalars().all()
    away = session.scalar(
        select(TeamMatch.team_id)
        .where(
            TeamMatch.competition_type == "club_league",
            TeamMatch.team_id != home,
            TeamMatch.team_id.not_in(opponents),
        )
        .limit(1)
    )
    return home, away


def test_zero_meetings_still_returns_both_team_blocks():
    s = SessionLocal()
    try:
        home, away = _two_teams_that_never_met(s)
        assert away is not None, "expected a never-met team to exist in the data"
        res = fixture_comparison(s, home_id=home, away_id=away, n=10)

        assert res["h2h"] == []          # no meetings — degrade gracefully
        assert len(res["home"]) > 0      # ...but each side's own form still renders
        assert len(res["away"]) > 0
    finally:
        s.close()


def test_fixture_detail_returns_exactly_both_sides():
    s = SessionLocal()
    try:
        home, away = _a_pair_with_meetings(s)
        fixture_id = s.scalar(
            select(TeamMatch.fixture_id)
            .where(TeamMatch.team_id == home, TeamMatch.opponent_id == away)
            .limit(1)
        )
        rows = fixture_detail(s, fixture_id=fixture_id)

        assert len(rows) == 2                                  # exactly two sides
        assert {r.fixture_id for r in rows} == {fixture_id}
        assert {r.team_id for r in rows} == {home, away}       # the two teams
    finally:
        s.close()


def test_fixture_detail_unknown_fixture_is_empty():
    s = SessionLocal()
    try:
        assert fixture_detail(s, fixture_id=-1) == []
    finally:
        s.close()
