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


def test_espn_leagues_cover_the_four_tiers():
    assert set(ESPN_LEAGUES) == {
        "Premier League", "Championship", "League One", "League Two",
    }


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
