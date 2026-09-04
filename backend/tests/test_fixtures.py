"""Behaviour tests for the Fixture-view query layer (app/fixtures.py).

Function-level, against the real DB (rolled back where it writes — these only
read). Mirrors the test_stats.py style: pick real teams with data, assert the
contract against a direct query. See docs/adr/0005.

CONVENTION — every pick is ORDERED and its precondition ASSERTED.
An unordered ``LIMIT 1`` returns whatever Postgres reaches first, so the subject
changes whenever a table is rewritten and a failure cannot be reproduced. Worse,
a pick that silently returns None turns a missing-data problem into a confusing
error deep inside the code under test. Both bite for real: on 2026-08-24 four
sibling tests failed because live state had moved under them and the failure read
exactly like a broken feature.
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
        .order_by(TeamMatch.team_id)
        .limit(1)
    )
    assert home is not None, "no club_league team_match rows — nothing to test"
    away = session.scalar(
        select(TeamMatch.opponent_id)
        .where(TeamMatch.team_id == home)
        .order_by(TeamMatch.opponent_id)
        .limit(1)
    )
    assert away is not None, f"team {home} has no opponents — nothing to test"
    return home, away


def _a_cup_pair(session) -> tuple[int, int]:
    """A (team, opponent) pair where the team is guaranteed to have cup rows."""
    team = session.scalar(
        select(TeamMatch.team_id)
        .where(TeamMatch.competition_type == "club_cup")
        .order_by(TeamMatch.team_id)
        .limit(1)
    )
    assert team is not None, "no club_cup team_match rows — nothing to test"
    opponent = session.scalar(
        select(TeamMatch.opponent_id)
        .where(TeamMatch.team_id == team)
        .order_by(TeamMatch.opponent_id)
        .limit(1)
    )
    assert opponent is not None, f"team {team} has no opponents — nothing to test"
    return team, opponent


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


def test_team_block_widens_to_cup_scope_when_requested():
    """With scopes=(league, cup) the form window is the last-n games across
    BOTH scopes — cup ties enter the window and displace older league games."""
    s = SessionLocal()
    try:
        team, opp = _a_cup_pair(s)
        scopes = ("club_league", "club_cup")
        res = fixture_comparison(s, home_id=team, away_id=opp, n=50, scopes=scopes)

        direct = s.execute(
            select(TeamMatch.fixture_id)
            .where(
                TeamMatch.team_id == team,
                TeamMatch.competition_type.in_(scopes),
            )
            .order_by(TeamMatch.date.desc())
            .limit(50)
        ).all()
        assert [r.fixture_id for r in res["home"]] == [fid for (fid,) in direct]

        # the widened window actually contains at least one cup tie
        cup_ids = {
            fid for (fid,) in s.execute(
                select(TeamMatch.fixture_id).where(
                    TeamMatch.team_id == team,
                    TeamMatch.competition_type == "club_cup",
                )
            ).all()
        }
        assert cup_ids & {r.fixture_id for r in res["home"]}
    finally:
        s.close()


def test_team_block_defaults_to_league_only():
    """No scopes argument -> unchanged league-only behaviour (the API default)."""
    s = SessionLocal()
    try:
        team, opp = _a_cup_pair(s)
        res = fixture_comparison(s, home_id=team, away_id=opp, n=50)
        types = {
            s.execute(
                select(TeamMatch.competition_type).where(
                    TeamMatch.fixture_id == r.fixture_id,
                    TeamMatch.team_id == team,
                )
            ).scalar_one()
            for r in res["home"]
        }
        assert types == {"club_league"}
    finally:
        s.close()


def test_h2h_block_is_both_sides_of_the_last_n_meetings():
    s = SessionLocal()
    try:
        home, away = _a_pair_with_meetings(s)
        res = fixture_comparison(s, home_id=home, away_id=away, n=5)

        # H2H spans every team-level scope (league + domestic cups) — the
        # oracle deliberately has no competition_type filter.
        meeting_ids = [
            fid for (fid,) in s.execute(
                select(TeamMatch.fixture_id)
                .where(
                    TeamMatch.team_id == home,
                    TeamMatch.opponent_id == away,
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
        .order_by(TeamMatch.team_id)
        .limit(1)
    )
    assert home is not None, "no club_league team_match rows — nothing to test"
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
        .order_by(TeamMatch.team_id)
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


def test_h2h_includes_cup_meetings():
    """A pair that met in a domestic cup shows that tie in H2H (meetings span
    all team-level scopes; each row carries its competition label)."""
    s = SessionLocal()
    try:
        cup_row = s.execute(
            select(TeamMatch.team_id, TeamMatch.opponent_id, TeamMatch.fixture_id)
            .where(TeamMatch.competition_type == "club_cup")
            .order_by(TeamMatch.fixture_id, TeamMatch.team_id)
            .limit(1)
        ).one()
        home, away, cup_fixture = cup_row
        res = fixture_comparison(s, home_id=home, away_id=away, n=50)
        got = {r.fixture_id for r in res["h2h"]}
        assert cup_fixture in got
        comps = {r.fixture_id: r.competition for r in res["h2h"]}
        assert comps[cup_fixture] in ("FA Cup", "EFL Cup", "Championship Play-offs")
    finally:
        s.close()


def test_upcoming_fixtures_window_and_status():
    """Scheduled fixtures inside the window appear (with team names); fixtures
    beyond the window don't. Rolled back."""
    import datetime as dt

    from app.fixtures import upcoming_fixtures
    from app.models.facts import Fixture
    from app.models.reference import Competition, Team

    s = SessionLocal()
    try:
        comp = s.scalars(
            select(Competition).where(Competition.name == "Premier League")
        ).one()
        t1, t2 = s.scalars(select(Team).limit(2)).all()
        now = dt.datetime.now(dt.timezone.utc)
        near = Fixture(
            competition_id=comp.id, season="9899", status="scheduled",
            date=now + dt.timedelta(days=2),
            home_team_id=t1.id, away_team_id=t2.id,
        )
        far = Fixture(
            competition_id=comp.id, season="9899", status="scheduled",
            date=now + dt.timedelta(days=40),
            home_team_id=t2.id, away_team_id=t1.id,
        )
        s.add_all([near, far])
        s.flush()

        rows = upcoming_fixtures(s, days=7)
        by_id = {r.fixture_id: r for r in rows}
        assert near.id in by_id and far.id not in by_id
        got = by_id[near.id]
        assert got.home_name == t1.canonical_name
        assert got.away_name == t2.canonical_name
        assert got.competition == "Premier League"
        assert all(r.date >= now for r in rows)
        s.rollback()
    finally:
        s.close()


def test_fixture_detail_returns_exactly_both_sides():
    s = SessionLocal()
    try:
        home, away = _a_pair_with_meetings(s)
        fixture_id = s.scalar(
            select(TeamMatch.fixture_id)
            .where(TeamMatch.team_id == home, TeamMatch.opponent_id == away)
            .order_by(TeamMatch.fixture_id)
            .limit(1)
        )
        assert fixture_id is not None, f"{home} v {away} have no meeting to detail"
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
