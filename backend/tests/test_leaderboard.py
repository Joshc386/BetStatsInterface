"""Behaviour tests for the Leaderboard query layer (app/leaderboard.py, ADR 0019).

Known-output: a synthetic league inside a rolled-back transaction, every figure
worked out by hand from CONTEXT.md ("Club window", "Leaderboard", "Cards").

Albion (A) v Borough (B), one game a day in January 2001:
    d1-d5    "Zz Lower League", season T0   <- last season, a division down
    d6-d12   "Zz League", season T1         <- this season
    d13      "Zz League", T1: team rows only, player data not yet published
  -> each club's window = d3..d12: the last 10 League games WITH player data,
     crossing into the lower league; d13 skipped and labelled; d1-d2 outside.

Rovers v Outfield played 5 "Zz League" games in December 2000 (season T0) and
nothing since: not this season's league, so never on its board.

    Zz Fuller   A  90' d1-d12; 5 shots on d1-d2, 2 on d3-d12 -> 20 in 900' = 2.00
    Zz Halfway  A  45' d3-d12, 1 shot each                   -> 10 in 450' = 2.00
                   (exactly the 450' minimum; ties Fuller, fewer minutes)
    Zz Cameo    A  10' d1-d12, 1 shot each -> 100' in window: under the minimum
    Zz Carder   A  90' d3-d12; d4 two yellows then red, d9 a yellow
                   -> Cards 3 in 900' = 0.30, carded in 2 of 10
    Zz Mover    B  90' d3-d7, 3 shots each; then A 90' d8-d12, 1 shot each
                   -> listed under A only: 5 in 450' = 1.00
    Zz Bee      B  90' d1-d12, nothing                       -> 0.00
    Zz Rover    R  90' in all 5, 9 shots each               -> never listed
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.leaderboard import leaderboard
from app.models.facts import Fixture, PlayerMatch, TeamMatch
from app.models.reference import Competition, Player, Team

UTC = dt.timezone.utc


@pytest.fixture
def board_session():
    """A session holding the synthetic league; everything is rolled back."""
    with SessionLocal() as session:
        league = Competition(name="Zz League", type="club_league")
        lower = Competition(name="Zz Lower League", type="club_league")
        teams = {n: Team(canonical_name=f"Zz {n}")
                 for n in ("Albion", "Borough", "Rovers", "Outfield")}
        players = {n: Player(canonical_name=f"Zz {n}")
                   for n in ("Fuller", "Halfway", "Cameo", "Carder", "Mover", "Bee", "Rover")}
        session.add_all([league, lower, *teams.values(), *players.values()])
        session.flush()

        def game(comp, season, when, home, away):
            f = Fixture(
                competition_id=comp.id, season=season, date=when,
                home_team_id=teams[home].id, away_team_id=teams[away].id,
                status="finished", stage=when.isoformat(),
            )
            session.add(f)
            session.flush()
            for team, opp, is_home in ((home, away, True), (away, home, False)):
                session.add(TeamMatch(
                    fixture_id=f.id, competition_id=comp.id, competition_type="club_league",
                    season=season, date=when, team_id=teams[team].id,
                    opponent_id=teams[opp].id, is_home=is_home, source="fdcouk", gf=0, ga=0,
                ))
            return f

        def appear(f, name, team, minutes, *, shots=0, y=0, r=0):
            home = f.home_team_id == teams[team].id
            session.add(PlayerMatch(
                fixture_id=f.id, competition_id=f.competition_id,
                competition_type="club_league", season=f.season, date=f.date,
                player_id=players[name].id, team_id=teams[team].id,
                opponent_id=f.away_team_id if home else f.home_team_id,
                is_home=home, minutes=minutes, goals=0, assists=0, shots=shots, sot=0,
                tackles=0, fouls_committed=0, fouls_drawn=0, yellows=y, reds=r,
            ))

        day = {}
        for d in range(1, 13):
            comp, season = (lower, "T0") if d <= 5 else (league, "T1")
            home, away = ("Albion", "Borough") if d % 2 else ("Borough", "Albion")
            f = day[d] = game(comp, season, dt.datetime(2001, 1, d, 15, tzinfo=UTC), home, away)
            appear(f, "Fuller", "Albion", 90, shots=5 if d <= 2 else 2)
            appear(f, "Cameo", "Albion", 10, shots=1)
            appear(f, "Bee", "Borough", 90)
            if d >= 3:
                appear(f, "Halfway", "Albion", 45, shots=1)
                appear(f, "Carder", "Albion", 90, y={4: 2, 9: 1}.get(d, 0), r=1 if d == 4 else 0)
                if d <= 7:
                    appear(f, "Mover", "Borough", 90, shots=3)
                else:
                    appear(f, "Mover", "Albion", 90, shots=1)
        # d13: team rows only -- nobody appears, as before FBref publishes
        day[13] = game(league, "T1", dt.datetime(2001, 1, 13, 15, tzinfo=UTC), "Albion", "Borough")
        for d in range(1, 6):
            f = game(league, "T0", dt.datetime(2000, 12, d, 15, tzinfo=UTC), "Rovers", "Outfield")
            appear(f, "Rover", "Rovers", 90, shots=9)
        session.flush()
        try:
            yield session, league, teams, day
        finally:
            session.rollback()


def _rows(board, metric):
    return [(r["player"], r["per90"], r["total"], r["minutes"], r["apps"])
            for r in board["categories"][metric]]


def test_shots_rank_per_90_over_the_club_window(board_session):
    session, league, _, _ = board_session
    board = leaderboard(session, competition_id=league.id)
    assert _rows(board, "shots")[:3] == [
        ("Zz Fuller", 2.0, 20, 900, 10),   # d1-d2's 5-shot games are outside
        ("Zz Halfway", 2.0, 10, 450, 10),  # level on rate: more minutes first
        ("Zz Mover", 1.0, 5, 450, 5),      # only his games for his latest club
    ]


def test_under_half_the_window_minutes_is_not_ranked(board_session):
    session, league, _, _ = board_session
    board = leaderboard(session, competition_id=league.id)
    # 100' in a 10-game window: 1 shot per 10' would top the list at 9.00
    assert all(r["player"] != "Zz Cameo" for rows in board["categories"].values() for r in rows)
    # exactly half the window (450') is enough
    assert "Zz Halfway" in [r["player"] for r in board["categories"]["shots"]]


def test_only_clubs_in_the_leagues_current_season_are_on_its_board(board_session):
    session, league, _, _ = board_session
    shots = leaderboard(session, competition_id=league.id)["categories"]["shots"]
    # Rovers played this league last season only; his 9 a game would lead it
    assert [r["player"] for r in shots] == [
        "Zz Fuller", "Zz Halfway", "Zz Mover", "Zz Bee", "Zz Carder",
    ]  # level on 0.00 and 900': by name


def test_a_player_is_listed_once_under_his_latest_club(board_session):
    session, league, teams, _ = board_session
    shots = leaderboard(session, competition_id=league.id)["categories"]["shots"]
    mover = [r for r in shots if r["player"] == "Zz Mover"]
    assert len(mover) == 1 and mover[0]["team_id"] == teams["Albion"].id


def test_cards_rank_per_90_and_say_how_often_he_was_carded(board_session):
    session, league, _, _ = board_session
    cards = leaderboard(session, competition_id=league.id)["categories"]["cards"]
    top = cards[0]
    # two yellows then red = 2 Cards, plus a yellow = 3 in 900'
    assert (top["player"], top["per90"], top["total"]) == ("Zz Carder", 0.3, 3)
    assert (top["carded_apps"], top["apps"]) == (2, 10)
    assert all(r["carded_apps"] is None for r in leaderboard(
        session, competition_id=league.id)["categories"]["shots"])


def test_each_club_says_what_its_window_covers(board_session):
    session, league, teams, day = board_session
    board = leaderboard(session, competition_id=league.id)
    assert board["competition"] == "Zz League"
    clubs = {c["team_id"]: c for c in board["clubs"]}
    assert set(clubs) == {teams["Albion"].id, teams["Borough"].id}
    albion = clubs[teams["Albion"].id]
    assert (albion["team"], albion["games"], albion["min_minutes"]) == ("Zz Albion", 10, 450)
    # d3-d5 were last season, a division down: the window crosses it
    assert albion["competitions"] == ["Zz League", "Zz Lower League"]
    # d13 has team rows but no player data yet: skipped and named
    assert albion["awaiting"] == [
        {"fixture_id": day[13].id, "date": day[13].date, "opponent": "Zz Borough"}
    ]


def test_endpoint_serves_a_league_and_404s_anything_else(board_session):
    """Handler called directly, as in test_table.py."""
    from fastapi import HTTPException

    from app.main import leaderboard_endpoint

    session, league, _, _ = board_session
    board = leaderboard_endpoint(competition_id=league.id, session=session)
    assert board.categories["shots"][0].player == "Zz Fuller"
    assert board.clubs[0].awaiting[0].opponent == "Zz Borough"
    cup = session.scalar(select(Competition.id).where(Competition.name == "FA Cup"))
    assert cup is not None, "competitions not seeded"
    for competition_id in (cup, -1):  # a cup has no club window; unknown id
        with pytest.raises(HTTPException) as e:
            leaderboard_endpoint(competition_id=competition_id, session=session)
        assert e.value.status_code == 404
