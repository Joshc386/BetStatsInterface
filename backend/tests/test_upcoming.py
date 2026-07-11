"""Tests for upcoming-fixture ingestion from the ESPN scoreboard (ADR 0009).

Parsing and season derivation are pure (a checked-in trimmed sample of the real
JSON shape). Resolution and upsert tests run against the real DB in rolled-back
sessions — nothing is left behind.
"""

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.facts import Fixture
from app.models.reference import Competition, Team
from ingestion.upcoming import (
    ESPN_LEAGUES,
    UnknownEspnTeamError,
    parse_scoreboard,
    purge_stale_international_placeholders,
    resolve_espn_team,
    season_for,
    upsert_scheduled,
)

SAMPLE = json.loads(
    (Path(__file__).parent / "fixtures" / "espn_scoreboard_sample.json").read_text(
        encoding="utf-8"
    )
)


def test_parse_scoreboard_keeps_only_scheduled_events():
    """3 events in the sample, 1 already played -> 2 scheduled parsed rows."""
    events = parse_scoreboard(SAMPLE)
    assert len(events) == 2
    first = events[0]
    assert first.home_espn_id == "359" and first.away_espn_id == "388"
    assert first.home_names == ("Arsenal", "Arsenal")
    assert first.away_names == ("Coventry City", "Coventry")
    assert first.date == dt.datetime(2026, 8, 21, 19, 0, tzinfo=dt.timezone.utc)


def test_season_for_rolls_in_july():
    """English season code: July onward belongs to the season starting that year."""
    assert season_for(dt.datetime(2026, 8, 21, tzinfo=dt.timezone.utc)) == "2627"
    assert season_for(dt.datetime(2027, 5, 24, tzinfo=dt.timezone.utc)) == "2627"
    assert season_for(dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)) == "2526"
    assert season_for(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)) == "2627"


def test_espn_leagues_cover_the_four_tiers_plus_world_cup():
    assert set(ESPN_LEAGUES) == {
        "Premier League", "Championship", "League One", "League Two",
        "World Cup",
    }


def _wc_event(home: tuple[str, str, str], away: tuple[str, str, str],
              date: str = "2026-07-14T19:00Z") -> dict:
    """A minimal scheduled scoreboard event; sides are (id, display, short)."""
    def side(t, home_away):
        return {"homeAway": home_away,
                "team": {"id": t[0], "displayName": t[1], "shortDisplayName": t[2]}}
    return {
        "date": date,
        "status": {"type": {"name": "STATUS_SCHEDULED"}},
        "competitions": [{"competitors": [side(home, "home"), side(away, "away")],
                          "status": {"type": {"name": "STATUS_SCHEDULED"}}}],
    }


def test_parse_scoreboard_drops_undecided_knockout_slots():
    """ESPN models an undecided knockout side as a pseudo-team ('Quarterfinal 2
    Winner'); such events are dropped — the semi appears on the first run after
    the QF resolves it, never as a fixture with a fake team."""
    payload = {"events": [
        _wc_event(("164", "Spain", "Spain"), ("459", "Belgium", "Belgium")),
        _wc_event(("478", "France", "France"),
                  ("17629", "Quarterfinal 2 Winner", "QF W2")),
        _wc_event(("5958", "Semifinal 1 Loser", "SF L1"),
                  ("5959", "Semifinal 2 Loser", "SF L2")),
    ]}
    events = parse_scoreboard(payload)
    assert len(events) == 1
    assert events[0].home_names == ("Spain", "Spain")


def test_resolve_espn_team_by_name_stamps_id():
    """First contact: no espn_id stored -> matched by normalised name
    (display or short), and the ESPN id is stamped for id-first resolution
    next run. Rolled back."""
    with SessionLocal() as session:
        team = resolve_espn_team(session, "999359", ("Arsenal", "Arsenal"))
        assert team.canonical_name == "Arsenal"
        assert team.espn_id == "999359"
        session.rollback()


def test_resolve_espn_team_prefers_stored_id():
    """A stored espn_id wins regardless of what ESPN calls the team today."""
    with SessionLocal() as session:
        arsenal = session.scalars(
            select(Team).where(Team.canonical_name == "Arsenal")
        ).one()
        arsenal.espn_id = "359"
        session.flush()
        team = resolve_espn_team(session, "359", ("Renamed FC", "Renamed"))
        assert team.id == arsenal.id
        session.rollback()


def test_resolve_espn_team_unknown_name_fails_loud():
    with SessionLocal() as session:
        with pytest.raises(UnknownEspnTeamError):
            resolve_espn_team(session, "424242", ("Melchester Rovers", "Melchester"))
        session.rollback()


def test_upsert_scheduled_creates_then_updates_never_demotes():
    """A new event creates a scheduled fixture; a kick-off change updates it in
    place; once the fixture is finished the feed can no longer touch it.
    Rolled back."""
    with SessionLocal() as session:
        comp = session.scalars(
            select(Competition).where(Competition.name == "Premier League")
        ).one()
        teams = session.scalars(select(Team).limit(2)).all()
        home, away = teams[0], teams[1]
        d1 = dt.datetime(2026, 8, 21, 19, 0, tzinfo=dt.timezone.utc)

        assert upsert_scheduled(session, comp, home.id, away.id, d1) == "created"
        fx = session.scalars(
            select(Fixture).where(
                Fixture.competition_id == comp.id,
                Fixture.season == "2627",
                Fixture.home_team_id == home.id,
                Fixture.away_team_id == away.id,
            )
        ).one()
        assert fx.status == "scheduled" and fx.date == d1

        d2 = d1 + dt.timedelta(days=1, hours=-3)  # TV reshuffle
        assert upsert_scheduled(session, comp, home.id, away.id, d2) == "updated"
        session.refresh(fx)
        assert fx.date == d2 and fx.status == "scheduled"

        fx.status = "finished"
        session.flush()
        assert upsert_scheduled(session, comp, home.id, away.id, d1) == "skipped_finished"
        session.refresh(fx)
        assert fx.status == "finished" and fx.date == d2  # untouched

        session.rollback()


def test_upsert_scheduled_international_uses_august_season_and_ignores_finished():
    """An international placeholder stores under the August-boundary season the
    FBref ingest will use (a July semi-final is season 2526, not 2627), and a
    finished meeting of the same pairing (a group game, stage-qualified) does
    NOT block it — the knockout rematch is a different game. Rolled back."""
    with SessionLocal() as session:
        comp = session.scalars(
            select(Competition).where(Competition.name == "World Cup")
        ).one()
        teams = session.scalars(select(Team).limit(2)).all()
        home, away = teams[0], teams[1]
        d = dt.datetime(2026, 7, 14, 19, 0, tzinfo=dt.timezone.utc)

        # a finished group meeting with the same orientation already exists
        session.add(Fixture(
            competition_id=comp.id, season="2526", date=d - dt.timedelta(days=20),
            home_team_id=home.id, away_team_id=away.id,
            status="finished", stage="Group stage",
        ))
        session.flush()

        assert upsert_scheduled(session, comp, home.id, away.id, d) == "created"
        placeholder = session.scalars(
            select(Fixture).where(
                Fixture.competition_id == comp.id,
                Fixture.home_team_id == home.id,
                Fixture.away_team_id == away.id,
                Fixture.status == "scheduled",
            )
        ).one()
        assert placeholder.season == "2526"  # August boundary, not season_for's 2627
        session.rollback()


def test_purge_removes_only_past_scheduled_internationals():
    """The purge deletes this competition's date-passed placeholders and nothing
    else: future placeholders and finished rows survive. Rolled back."""
    with SessionLocal() as session:
        comp = session.scalars(
            select(Competition).where(Competition.name == "World Cup")
        ).one()
        teams = session.scalars(select(Team).limit(4)).all()
        now = dt.datetime(2026, 7, 16, tzinfo=dt.timezone.utc)

        past = Fixture(competition_id=comp.id, season="2526",
                       date=now - dt.timedelta(days=1),
                       home_team_id=teams[0].id, away_team_id=teams[1].id,
                       status="scheduled")
        future = Fixture(competition_id=comp.id, season="2526",
                         date=now + dt.timedelta(days=3),
                         home_team_id=teams[2].id, away_team_id=teams[3].id,
                         status="scheduled")
        finished = Fixture(competition_id=comp.id, season="2526",
                           date=now - dt.timedelta(days=2),
                           home_team_id=teams[1].id, away_team_id=teams[0].id,
                           status="finished", stage="Group stage")
        session.add_all([past, future, finished])
        session.flush()

        # assert on OUR three rows only — the real DB may hold live placeholders
        # that are also (correctly) purged relative to the synthetic `now`
        assert purge_stale_international_placeholders(session, comp, now) >= 1
        remaining = session.scalars(
            select(Fixture).where(Fixture.id.in_([past.id, future.id, finished.id]))
        ).all()
        assert {f.id for f in remaining} == {future.id, finished.id}
        session.rollback()
