"""Behaviour tests for the Match report query layer (app/match_report.py).

Known-output: synthetic Fixtures with hand-built rows inside a rolled-back
transaction, so every figure below is worked out by hand from CONTEXT.md
("Match report", "Cards") — not re-derived by the code under test.

    M  league, finished, refereed
       home team row: 3-1, corners 7, shots 99, Y0 R1   <- football-data.co.uk
                      convention; the totals must NOT come from here
       home players:  Zz Bravo  90'  G1 A0 Sh3 SoT2 Tkl1 Fls2 Fld0  2Y + R (two yellows)
                      Zz Alpha  90'  G1 A1 Sh4 SoT2 Tkl3 Fls1 Fld2  1Y
                      Zz Echo   20'  all zero
          -> Sh7 SoT4 Fls3 Y3 R1 Cards 3; player goals 2 against a score of 3
             (an own goal is credited to no player)
       away players:  Zz Charlie 90' G1 straight red
                      Zz Delta   90' 1Y then straight red
          -> Y1 R2 Cards 3
    S  league, finished: the page published no shots/tackles/fouls (all NULL)
    W  league, finished: team rows only, player rows not yet published
    U  league, scheduled
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.match_report import match_report
from app.models.facts import Fixture, PlayerMatch, TeamMatch
from app.models.reference import Competition, Player, Referee, Team

UTC = dt.timezone.utc


@pytest.fixture
def report_session():
    """A session holding the synthetic Fixtures; everything is rolled back."""
    with SessionLocal() as session:
        league = session.scalar(
            select(Competition).where(Competition.name == "Premier League")
        )
        assert league is not None, "competitions not seeded"
        home_id, away_id = session.scalars(
            select(Team.id).order_by(Team.id).limit(2)
        ).all()

        ref = Referee(name="Zz Synthetic Referee")
        players = {n: Player(canonical_name=f"Zz {n}")
                   for n in ("Alpha", "Bravo", "Charlie", "Delta", "Echo")}
        session.add_all([ref, *players.values()])
        session.flush()

        def fixture(season, day, status="finished"):
            f = Fixture(
                competition_id=league.id, season=season,
                date=dt.datetime(2001, 1, day, 15, tzinfo=UTC),
                home_team_id=home_id, away_team_id=away_id, status=status,
                referee_id=ref.id if status == "finished" else None,
            )
            session.add(f)
            session.flush()
            return f

        m, s, w = fixture("T1", 1), fixture("T2", 8), fixture("T3", 15)
        u = fixture("T4", 22, "scheduled")

        def team_row(f, is_home, gf, ga, corners, shots, y, r):
            session.add(TeamMatch(
                fixture_id=f.id, competition_id=league.id,
                competition_type=league.type, season=f.season, date=f.date,
                team_id=home_id if is_home else away_id,
                opponent_id=away_id if is_home else home_id,
                is_home=is_home, source="fdcouk", gf=gf, ga=ga,
                corners=corners, shots=shots, yellows=y, reds=r,
            ))

        def player_row(f, name, is_home, minutes, g=0, a=0, sh=0, sot=0,
                       tkl=0, fls=0, fld=0, y=0, r=0):
            session.add(PlayerMatch(
                fixture_id=f.id, competition_id=league.id,
                competition_type=league.type, season=f.season, date=f.date,
                player_id=players[name].id,
                team_id=home_id if is_home else away_id,
                opponent_id=away_id if is_home else home_id,
                is_home=is_home, minutes=minutes, goals=g, assists=a,
                shots=sh, sot=sot, tackles=tkl, fouls_committed=fls,
                fouls_drawn=fld, yellows=y, reds=r,
            ))

        team_row(m, True, 3, 1, 7, 99, 0, 1)
        team_row(m, False, 1, 3, 2, 98, 1, 2)
        player_row(m, "Bravo", True, 90, g=1, sh=3, sot=2, tkl=1, fls=2, y=2, r=1)
        player_row(m, "Alpha", True, 90, g=1, a=1, sh=4, sot=2, tkl=3, fls=1, fld=2, y=1)
        player_row(m, "Echo", True, 20)
        player_row(m, "Charlie", False, 90, g=1, r=1)
        player_row(m, "Delta", False, 90, y=1, r=1)

        team_row(s, True, 0, 0, 4, 10, 1, 0)
        team_row(s, False, 0, 0, 5, 11, 0, 0)
        player_row(s, "Alpha", True, 90, sh=None, sot=None, tkl=None, fls=None, fld=None, y=1)
        player_row(s, "Charlie", False, 90, sh=None, sot=None, tkl=None, fls=None, fld=None)

        team_row(w, True, 2, 2, 6, 12, 2, 0)
        team_row(w, False, 2, 2, 3, 9, 1, 0)
        session.flush()
        try:
            yield session, {"m": m.id, "s": s.id, "w": w.id, "u": u.id}
        finally:
            session.rollback()


def test_totals_are_summed_from_player_rows_score_and_corners_from_team_row(report_session):
    session, ids = report_session
    r = match_report(session, fixture_id=ids["m"])
    home, away = r["home"], r["away"]
    assert (home["score"], away["score"]) == (3, 1)
    assert (home["corners"], away["corners"]) == (7, 2)
    assert home["totals"] == {
        "goals": 2, "assists": 1, "shots": 7, "sot": 4, "tackles": 4,
        "fouls_committed": 3, "fouls_drawn": 2, "yellows": 3, "reds": 1, "cards": 3,
    }
    # yellow then straight red is 2 Cards; a straight red is 1
    assert (away["totals"]["yellows"], away["totals"]["reds"], away["totals"]["cards"]) == (1, 2, 3)


def test_players_most_minutes_first_then_by_name(report_session):
    session, ids = report_session
    r = match_report(session, fixture_id=ids["m"])
    assert [p["player"] for p in r["home"]["players"]] == ["Zz Alpha", "Zz Bravo", "Zz Echo"]
    assert [p["player"] for p in r["away"]["players"]] == ["Zz Charlie", "Zz Delta"]


def test_two_yellow_dismissal_is_marked_and_counts_two_cards(report_session):
    session, ids = report_session
    by_name = {p["player"]: p for p in match_report(session, fixture_id=ids["m"])["home"]["players"]}
    bravo = by_name["Zz Bravo"]
    assert (bravo["yellows"], bravo["reds"], bravo["cards"]) == (2, 1, 2)
    assert bravo["second_yellow"] is True
    # a yellow then a straight red is NOT a second-yellow dismissal
    delta = {p["player"]: p for p in match_report(session, fixture_id=ids["m"])["away"]["players"]}["Zz Delta"]
    assert (delta["cards"], delta["second_yellow"]) == (2, False)
    assert by_name["Zz Alpha"]["second_yellow"] is False


def test_names_the_fixture_its_teams_and_its_recorded_referee(report_session):
    session, ids = report_session
    r = match_report(session, fixture_id=ids["m"])
    fixture = session.get(Fixture, ids["m"])
    assert r["fixture_id"] == ids["m"]
    assert (r["date"], r["season"]) == (fixture.date, "T1")
    assert (r["competition"], r["competition_type"], r["stage"]) == ("Premier League", "club_league", "")
    assert r["referee_id"] == fixture.referee_id and r["referee"] == "Zz Synthetic Referee"
    assert r["home"]["team_id"] == fixture.home_team_id
    assert r["away"]["team_id"] == fixture.away_team_id
    assert r["home"]["team"] == session.get(Team, fixture.home_team_id).canonical_name


def test_only_a_finished_fixture_has_a_match_report(report_session):
    session, ids = report_session
    assert match_report(session, fixture_id=ids["u"]) is None  # scheduled
    assert match_report(session, fixture_id=-1) is None  # unknown


def test_columns_the_page_did_not_publish_are_none_never_zero(report_session):
    session, ids = report_session
    r = match_report(session, fixture_id=ids["s"])
    totals = r["home"]["totals"]
    for m in ("shots", "sot", "tackles", "fouls_committed", "fouls_drawn"):
        assert totals[m] is None, m
        assert r["home"]["players"][0][m] is None, m
    assert (totals["yellows"], totals["cards"], totals["goals"]) == (1, 1, 0)


def test_a_game_awaiting_player_data_still_has_its_score_and_corners(report_session):
    session, ids = report_session
    r = match_report(session, fixture_id=ids["w"])
    assert (r["home"]["score"], r["away"]["score"]) == (2, 2)
    assert (r["home"]["corners"], r["away"]["corners"]) == (6, 3)
    for side in (r["home"], r["away"]):
        assert side["players"] == []
        assert set(side["totals"].values()) == {None}  # not known yet, never 0


def test_endpoint_serves_the_report_and_404s_an_unplayed_fixture(report_session):
    """Handler called directly, as in test_table.py."""
    from fastapi import HTTPException

    from app.main import fixture_match_report

    session, ids = report_session
    r = fixture_match_report(fixture_id=ids["m"], session=session)
    assert r.referee == "Zz Synthetic Referee"
    assert r.home.totals.cards == 3 and r.home.players[1].second_yellow is True
    for fixture_id in (ids["u"], -1):
        with pytest.raises(HTTPException) as e:
            fixture_match_report(fixture_id=fixture_id, session=session)
        assert e.value.status_code == 404
