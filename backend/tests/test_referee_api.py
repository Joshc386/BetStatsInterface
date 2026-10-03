"""Behaviour tests for the Referee hub query layer (app/referees.py, ADR 0018).

Known-output: a synthetic referee with hand-built Player-Match rows inside a
rolled-back transaction, so every figure below is worked out by hand from the
CONTEXT.md "Cards" definition — not re-derived by the code under test.

    A  league, oldest   home: P1 2Y+R (two-yellow dismissal), 3 fouls
                              P2 1Y, 1 foul
                        away: P3 straight R, 2 fouls
                              P4 1Y then straight R, 0 fouls
       -> home Cards 3 (Y3 R1), away Cards 3 (Y1 R2); match Cards 6, Y4, R3, fouls 6
    B  league           home: P1 1Y; away: P3 0  -- fouls NOT published (page NULL)
       -> Cards 1, Y1, R0, fouls unrecorded
    C  league, newest   no player rows at all (FBref published no lineups)
       -> counts as a match, recorded in no rate
    D  FA Cup           one yellow each side -- outside the default League scope
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.facts import Fixture, PlayerMatch
from app.models.reference import Competition, Player, Referee, Team
from app.referees import referee_list, referee_summary

UTC = dt.timezone.utc


@pytest.fixture
def ref_session():
    """A session holding the synthetic referee; everything is rolled back."""
    with SessionLocal() as session:
        league = session.scalar(
            select(Competition).where(Competition.name == "Premier League")
        )
        cup = session.scalar(select(Competition).where(Competition.name == "FA Cup"))
        assert league is not None and cup is not None, "competitions not seeded"
        home_id, away_id = session.scalars(
            select(Team.id).order_by(Team.id).limit(2)
        ).all()
        players = session.scalars(select(Player.id).order_by(Player.id).limit(4)).all()
        assert len(players) == 4, "need four players to build the fixture rows"

        ref = Referee(name="Zz Synthetic Referee")
        session.add(ref)
        session.flush()

        def fixture(comp, season, day):
            f = Fixture(
                competition_id=comp.id, season=season,
                date=dt.datetime(2001, 1, day, 15, tzinfo=UTC),
                home_team_id=home_id, away_team_id=away_id,
                status="finished", referee_id=ref.id,
            )
            session.add(f)
            session.flush()
            return f

        a = fixture(league, "T1", 1)
        b = fixture(league, "T2", 8)
        c = fixture(league, "T3", 15)
        d = fixture(cup, "T4", 10)

        def row(f, comp, player, is_home, y, r, fouls):
            session.add(PlayerMatch(
                fixture_id=f.id, competition_id=comp.id, competition_type=comp.type,
                season=f.season, date=f.date, player_id=player,
                team_id=home_id if is_home else away_id,
                opponent_id=away_id if is_home else home_id,
                is_home=is_home, minutes=90, yellows=y, reds=r,
                fouls_committed=fouls,
            ))

        p1, p2, p3, p4 = players
        row(a, league, p1, True, 2, 1, 3)
        row(a, league, p2, True, 1, 0, 1)
        row(a, league, p3, False, 0, 1, 2)
        row(a, league, p4, False, 1, 1, 0)
        row(b, league, p1, True, 1, 0, None)
        row(b, league, p3, False, 0, 0, None)
        row(d, cup, p1, True, 1, 0, 1)
        row(d, cup, p3, False, 1, 0, 1)
        session.flush()
        try:
            yield session, ref.id, {"a": a.id, "b": b.id, "c": c.id, "d": d.id}
        finally:
            session.rollback()


def test_league_window_rates_follow_the_cards_definition(ref_session):
    session, ref_id, _ = ref_session
    s = referee_summary(session, referee_id=ref_id)
    assert s["matches"] == 3  # A, B, C — the cup tie D is out of scope
    assert s["rates"]["cards"] == {"total": 7, "recorded": 2, "per_match": 3.5}
    assert s["rates"]["yellows"] == {"total": 5, "recorded": 2, "per_match": 2.5}
    assert s["rates"]["reds"] == {"total": 3, "recorded": 2, "per_match": 1.5}
    # only A published fouls; B's NULL page shrinks the sample, never scores 0
    assert s["rates"]["fouls"] == {"total": 6, "recorded": 1, "per_match": 6.0}


def test_two_yellow_dismissal_is_two_cards_one_red(ref_session):
    session, ref_id, ids = ref_session
    games = {g["fixture_id"]: g for g in referee_summary(session, referee_id=ref_id)["games"]}
    home = games[ids["a"]]["home_side"]
    assert (home["cards"], home["yellows"], home["reds"]) == (3, 3, 1)
    away = games[ids["a"]]["away_side"]  # straight red + yellow-then-red
    assert (away["cards"], away["yellows"], away["reds"]) == (3, 1, 2)
    assert games[ids["a"]]["cards"] == 6 and games[ids["a"]]["fouls"] == 6


def test_a_side_with_any_blank_card_row_is_unrecorded_not_undercounted(ref_session):
    """Cards are never NULL today (ADR 0016), but if a page ever leaves one
    blank, SUM would silently skip it. A Metric counts for a side only when
    every row on that side carries it — the rule fouls already followed."""
    session, ref_id, ids = ref_session
    row = session.scalars(
        select(PlayerMatch)
        .where(PlayerMatch.fixture_id == ids["a"], PlayerMatch.is_home.is_(True))
        .order_by(PlayerMatch.id)
        .limit(1)
    ).one()
    row.yellows = None
    session.flush()
    games = {g["fixture_id"]: g for g in referee_summary(session, referee_id=ref_id)["games"]}
    a = games[ids["a"]]
    assert (a["cards"], a["yellows"]) == (None, None)
    assert a["home_side"]["cards"] is None and a["away_side"]["cards"] == 3


def test_unrecorded_metrics_are_none_not_zero(ref_session):
    session, ref_id, ids = ref_session
    games = {g["fixture_id"]: g for g in referee_summary(session, referee_id=ref_id)["games"]}
    assert games[ids["b"]]["fouls"] is None
    assert games[ids["b"]]["cards"] == 1
    c = games[ids["c"]]
    assert (c["cards"], c["yellows"], c["reds"], c["fouls"]) == (None, None, None, None)


def test_home_and_away_split_divides_by_the_same_recorded_matches(ref_session):
    session, ref_id, _ = ref_session
    s = referee_summary(session, referee_id=ref_id)
    assert s["home_rates"]["cards"] == 2.0  # (3 + 1) / 2
    assert s["away_rates"]["cards"] == 1.5  # (3 + 0) / 2
    assert s["home_rates"]["fouls"] == 4.0 and s["away_rates"]["fouls"] == 2.0


def test_hit_rate_counts_recorded_matches_only(ref_session):
    session, ref_id, _ = ref_session
    s = referee_summary(session, referee_id=ref_id, metric="cards", threshold=4)
    assert s["hit_rate"] == {
        "threshold": 4, "direction": "over", "hits": 1, "n": 2, "pct": 50.0,
    }
    under = referee_summary(
        session, referee_id=ref_id, metric="cards", threshold=4, direction="under"
    )
    assert under["hit_rate"]["hits"] == 1


def test_games_are_chronological_newest_last(ref_session):
    session, ref_id, ids = ref_session
    order = [g["fixture_id"] for g in referee_summary(session, referee_id=ref_id)["games"]]
    assert order == [ids["a"], ids["b"], ids["c"]]


def test_last_n_takes_the_most_recent_matches(ref_session):
    session, ref_id, ids = ref_session
    s = referee_summary(session, referee_id=ref_id, n=1)
    assert [g["fixture_id"] for g in s["games"]] == [ids["c"]]
    assert s["rates"]["cards"] == {"total": 0, "recorded": 0, "per_match": None}


def test_season_window(ref_session):
    session, ref_id, ids = ref_session
    s = referee_summary(session, referee_id=ref_id, seasons=["T1", "T2"])
    assert [g["fixture_id"] for g in s["games"]] == [ids["a"], ids["b"]]


def test_scope_is_league_by_default_and_explicit_otherwise(ref_session):
    session, ref_id, ids = ref_session
    cup = referee_summary(session, referee_id=ref_id, scope="club_cup")
    assert [g["fixture_id"] for g in cup["games"]] == [ids["d"]]
    assert referee_summary(session, referee_id=ref_id, scope="all")["matches"] == 4
    assert referee_summary(session, referee_id=ref_id)["scope_counts"] == {
        "club_league": 3, "club_cup": 1,
    }


def test_domestic_referee_is_not_covered_ties_only(ref_session):
    session, ref_id, _ = ref_session
    assert referee_summary(session, referee_id=ref_id)["covered_ties_only"] is False


def test_unknown_referee_is_none(ref_session):
    session, _, _ = ref_session
    assert referee_summary(session, referee_id=-1) is None


def test_referee_list_counts_matches_and_puts_the_most_recent_first(ref_session):
    session, ref_id, _ = ref_session
    listed = {r["id"]: r for r in referee_list(session)}
    assert listed[ref_id]["matches"] == 4
    dates = [r["last_date"] for r in referee_list(session)]
    assert dates == sorted(dates, reverse=True)


def test_search_finds_referees_alongside_teams_and_players(ref_session):
    """Handler called directly, like test_table: the synthetic referee is
    visible only inside this session."""
    from app.main import search

    session, ref_id, _ = ref_session
    hits = search(q="Zz Synthetic", limit=20, session=session)
    assert [(h.entity, h.id) for h in hits] == [("referee", ref_id)]


def test_search_hides_a_referee_with_no_recorded_fixture(ref_session):
    """An alias added later strands the old spelling's row; it must not
    surface as a referee with no matches."""
    from app.main import search

    session, _, _ = ref_session
    session.add(Referee(name="Zz Orphaned Spelling"))
    session.flush()
    assert search(q="Zz Orphaned", limit=20, session=session) == []


def test_fixture_detail_rows_name_the_referee(ref_session):
    from app.fixtures import fixture_detail
    from app.models.facts import TeamMatch

    session, ref_id, ids = ref_session
    fixture = session.get(Fixture, ids["a"])
    for is_home in (True, False):
        session.add(TeamMatch(
            fixture_id=fixture.id, competition_id=fixture.competition_id,
            competition_type="club_league", season=fixture.season, date=fixture.date,
            team_id=fixture.home_team_id if is_home else fixture.away_team_id,
            opponent_id=fixture.away_team_id if is_home else fixture.home_team_id,
            is_home=is_home, source="fbref",
        ))
    session.flush()
    rows = fixture_detail(session, fixture_id=fixture.id)
    assert {(r.referee_id, r.referee) for r in rows} == {(ref_id, "Zz Synthetic Referee")}


def test_summary_endpoint_guards_metric_scope_and_unknown_referee(ref_session):
    from fastapi import HTTPException

    from app.main import referee_summary_endpoint

    session, ref_id, _ = ref_session
    call = dict(n=10, seasons=None, threshold=None, direction="over", session=session)
    ok = referee_summary_endpoint(ref_id, metric="fouls", scope="all", **call)
    assert ok.matches == 4 and ok.rates["fouls"].recorded == 2
    for kwargs, status in (
        ({"metric": "corners", "scope": "club_league"}, 404),
        ({"metric": "cards", "scope": "friendlies"}, 422),
    ):
        with pytest.raises(HTTPException) as exc:
            referee_summary_endpoint(ref_id, **kwargs, **call)
        assert exc.value.status_code == status
    with pytest.raises(HTTPException) as exc:
        referee_summary_endpoint(-1, metric="cards", scope="club_league", **call)
    assert exc.value.status_code == 404
