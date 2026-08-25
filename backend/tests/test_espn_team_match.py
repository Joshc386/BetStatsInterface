"""Tests for ESPN-sourced league Team-Match rows (docs/adr/0015).

Parsing is pure and checked against a trimmed sample of the real payload
(Brighton 4-0 Aston Villa, 2026-08-22). The write path runs against the real DB
in rolled-back sessions — nothing is committed.
"""

import copy
import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import delete, select, update

from app.db import SessionLocal
from app.models.facts import Fixture, TeamMatch
from app.models.reference import Competition, Team
from ingestion.espn_team_match import (
    EspnFixtureMismatch,
    EspnStatsUnavailable,
    parse_summary,
    pending_fixtures,
    write_team_rows,
)
from ingestion import espn_team_match
from ingestion.upcoming import (
    parse_scoreboard,
    run_espn_team_rows,
    upsert_event,
)

SAMPLE = json.loads(
    (Path(__file__).parent / "fixtures" / "espn_summary_sample.json").read_text(
        encoding="utf-8"
    )
)
SCOREBOARD = json.loads(
    (Path(__file__).parent / "fixtures" / "espn_scoreboard_sample.json").read_text(
        encoding="utf-8"
    )
)


def test_parse_summary_maps_both_sides_home_first():
    """The tracer bullet: ESPN's stat names become our Metric names, goals come
    from the header rather than the stat block, and each side's conceded
    figures are the *other* side's shots — one request covers both teams."""
    home, away = parse_summary(SAMPLE)

    assert home.espn_id == "331" and away.espn_id == "362"
    assert (home.goals, away.goals) == (4, 0)

    assert home.shots == 21 and home.sot == 6
    assert home.fouls == 12 and home.corners == 5
    assert home.yellows == 2 and home.reds == 0

    # Conceded is the opposite side's attacking figures, not a separate stat.
    assert home.shots_conceded == away.shots
    assert home.sot_conceded == away.sot
    assert away.shots_conceded == home.shots
    assert away.sot_conceded == home.sot


def test_zero_filled_or_absent_stats_raise_rather_than_writing_zeros():
    """ESPN returns a zero-filled stat block for some (mostly older) fixtures
    rather than omitting it — 4 of 25 audited fixtures did. A real match never
    has zero shots AND zero fouls, so that shape means "no data", and writing
    it would silently enter a 0-shot game into every Rolling Window."""
    zero_filled = copy.deepcopy(SAMPLE)
    for team in zero_filled["boxscore"]["teams"]:
        for stat in team["statistics"]:
            stat["displayValue"] = "0"
    with pytest.raises(EspnStatsUnavailable):
        parse_summary(zero_filled)

    missing = copy.deepcopy(SAMPLE)
    missing["boxscore"]["teams"] = []
    with pytest.raises(EspnStatsUnavailable):
        parse_summary(missing)


# --- the ESPN event id is stamped, never re-derived -------------------------


def test_parse_scoreboard_carries_the_espn_event_id():
    """Without the event id the team-stat writer would have to re-fetch a
    scoreboard to rediscover an identity `upcoming` already had. This project
    resolves an identity once and stamps it (docs/adr/0015)."""
    events = parse_scoreboard(SCOREBOARD)
    assert events, "sample has no scheduled events"
    assert all(e.espn_event_id for e in events)


def test_upsert_event_stamps_the_event_id_on_the_fixture():
    with SessionLocal() as session:
        competition = session.scalars(
            select(Competition).where(Competition.name == "Premier League")
        ).one()
        home, away = session.scalars(
            select(Team).where(Team.espn_id.is_not(None)).limit(2)
        ).all()
        outcome = upsert_event(
            session,
            competition,
            home.id,
            away.id,
            dt.datetime(2027, 5, 30, 15, 0, tzinfo=dt.timezone.utc),
            espn_event_id="999999999",
        )
        assert outcome == "created"
        fixture = session.scalars(
            select(Fixture).where(Fixture.espn_event_id == "999999999")
        ).one()
        assert fixture.home_team_id == home.id
        session.rollback()


# --- the write path (rolled back; nothing is committed) ---------------------


def _brighton_villa(session) -> Fixture:
    """The Fixture the sample payload describes: Brighton (331) v Villa (362).

    These tests need a Fixture that football-data.co.uk has NOT published, and
    this one qualified when they were written on 2026-08-23. That was a claim
    about live DB state with a shelf life, and it expired on 2026-08-24 when
    fd.co.uk resumed publishing and reclaimed the Fixture — every write test
    then returned 'skipped_fdcouk' and read exactly like a broken feature.

    So the precondition is now ESTABLISHED rather than assumed: any existing
    rows are cleared inside the transaction. Every test here rolls back, so
    the real rows are untouched.
    """
    fixture = session.scalars(select(Fixture).where(Fixture.id == 29033)).one()
    session.execute(delete(TeamMatch).where(TeamMatch.fixture_id == fixture.id))
    session.flush()
    return fixture


def test_write_team_rows_writes_both_sides_stamped_espn():
    with SessionLocal() as session:
        fixture = _brighton_villa(session)
        home, away = parse_summary(SAMPLE)

        assert write_team_rows(session, fixture, home, away) == "written"

        rows = session.scalars(
            select(TeamMatch).where(TeamMatch.fixture_id == fixture.id)
        ).all()
        assert len(rows) == 2
        assert {r.source for r in rows} == {"espn"}

        home_row = next(r for r in rows if r.is_home)
        assert (home_row.gf, home_row.ga) == (4, 0)
        assert home_row.shots == 21 and home_row.corners == 5
        assert home_row.shots_conceded == away.shots
        assert home_row.competition_type == "club_league"
        assert home_row.season == fixture.season

        session.rollback()


def test_espn_never_overwrites_a_fdcouk_row():
    """The flip-flop guard. football-data.co.uk's upsert is unconditional and
    it is the historical authority, so if ESPN also overwrote, the two writers
    would fight over the same row on every run, forever."""
    with SessionLocal() as session:
        fixture = _brighton_villa(session)
        home, away = parse_summary(SAMPLE)
        assert write_team_rows(session, fixture, home, away) == "written"

        # football-data.co.uk publishes and reclaims the Fixture.
        session.execute(
            update(TeamMatch)
            .where(TeamMatch.fixture_id == fixture.id)
            .values(source="fdcouk", shots=99)
        )
        session.flush()

        assert write_team_rows(session, fixture, home, away) == "skipped_fdcouk"
        rows = session.scalars(
            select(TeamMatch).where(TeamMatch.fixture_id == fixture.id)
        ).all()
        assert {r.source for r in rows} == {"fdcouk"}
        assert all(r.shots == 99 for r in rows), "ESPN clobbered fd.co.uk's values"

        session.rollback()


def test_writing_twice_is_idempotent():
    """Every slot re-examines recent Fixtures, so this runs repeatedly over the
    same match. Two runs must leave one pair of rows with identical values."""
    with SessionLocal() as session:
        fixture = _brighton_villa(session)
        home, away = parse_summary(SAMPLE)

        write_team_rows(session, fixture, home, away)
        first = sorted(
            (r.team_id, r.gf, r.ga, r.shots, r.corners)
            for r in session.scalars(
                select(TeamMatch).where(TeamMatch.fixture_id == fixture.id)
            )
        )
        assert write_team_rows(session, fixture, home, away) == "written"
        second = sorted(
            (r.team_id, r.gf, r.ga, r.shots, r.corners)
            for r in session.scalars(
                select(TeamMatch).where(TeamMatch.fixture_id == fixture.id)
            )
        )
        assert len(second) == 2 and first == second

        session.rollback()


def test_a_summary_for_the_wrong_fixture_fails_loud():
    """Attaching one Fixture's figures to another is precisely the corruption
    found in football-data.co.uk (Hull v Preston carrying Watford v Millwall's
    numbers). It is undetectable once written, so refuse rather than guess."""
    with SessionLocal() as session:
        fixture = _brighton_villa(session)
        home, away = parse_summary(SAMPLE)
        impostor = replace(away, espn_id="999999")
        with pytest.raises(EspnFixtureMismatch):
            write_team_rows(session, fixture, home, impostor)
        session.rollback()


def test_a_finished_fixture_still_gets_its_event_id_stamped():
    """`upsert_event` deliberately never re-dates a finished Fixture, but the
    ESPN id is identity, not schedule. Without this the entire existing backlog
    of played-but-unpublished Fixtures — the ones this ADR exists to rescue —
    could never be stamped, so the team-stat writer would never see them."""
    with SessionLocal() as session:
        fixture = _brighton_villa(session)
        assert fixture.status == "finished"
        # Reset inside the transaction: the real job may already have stamped
        # this row, and a test that reads live state passes only until it does.
        fixture.espn_event_id = None
        session.flush()

        competition = session.get(Competition, fixture.competition_id)
        outcome = upsert_event(
            session,
            competition,
            fixture.home_team_id,
            fixture.away_team_id,
            fixture.date,
            finished=True,
            espn_event_id="401879297",
        )
        assert outcome == "skipped_finished"
        session.flush()
        session.refresh(fixture)
        assert fixture.espn_event_id == "401879297"

        session.rollback()


def test_pending_fixtures_wants_played_league_games_with_no_team_rows():
    """What the job goes looking for: a league Fixture that was played, carries
    an ESPN id, and has no Team-Match row yet. A Fixture football-data.co.uk
    has already published is not pending — it is done."""
    with SessionLocal() as session:
        fixture = _brighton_villa(session)
        fixture.espn_event_id = "401879297"
        # Same reason: once the real job has run this Fixture HAS rows, so
        # "is it pending?" must be asked of a known state, not the live one.
        session.execute(
            delete(TeamMatch).where(TeamMatch.fixture_id == fixture.id)
        )
        session.flush()

        assert fixture.id in {f.id for f in pending_fixtures(session)}

        home, away = parse_summary(SAMPLE)
        write_team_rows(session, fixture, home, away)
        assert fixture.id not in {f.id for f in pending_fixtures(session)}, (
            "a Fixture we already hold rows for is not pending"
        )

        session.rollback()


def test_pending_fixtures_ignores_cups_and_internationals():
    """ESPN team stats are a club_league decision only. Cup, European and
    international team rows come from cached FBref pages (ADR 0008/0011) and
    must not be touched."""
    with SessionLocal() as session:
        pending = pending_fixtures(session)
        types = {
            session.get(Competition, f.competition_id).type for f in pending
        }
        assert types <= {"club_league"}
        session.rollback()


# --- wiring: upcoming owns the ESPN team rows (docs/adr/0015) ---------------


def test_upcoming_reports_the_espn_team_rows_it_wrote(monkeypatch):
    """`upcoming` marks a Fixture finished, so it is the job that knows the
    moment a match becomes ingestable — and it already holds the payload.
    Wiring the write here means no new scheduled job and no extra slate fetch.
    """
    calls = {}

    def fake_ingest(season=None, log=print):
        calls["ran"] = True
        return {"written": 4, "skipped_fdcouk": 1, "no_stats": 0}

    monkeypatch.setattr(espn_team_match, "ingest", fake_ingest)
    report = run_espn_team_rows(log=lambda *a, **k: None)

    assert calls.get("ran") is True
    assert report == {"written": 4, "skipped_fdcouk": 1, "no_stats": 0}


def test_espn_team_row_failure_never_takes_down_the_slate(monkeypatch):
    """The slate is the load-bearing output — matchday's pending probe depends
    on it (ADR 0014). A team-stat write is an enrichment, so its failure is
    logged and reported, never raised: losing the slate would silently stall
    the FBref player pipeline too."""
    def boom(season=None, log=print):
        raise RuntimeError("ESPN summary endpoint down")

    monkeypatch.setattr(espn_team_match, "ingest", boom)
    report = run_espn_team_rows(log=lambda *a, **k: None)

    assert "error" in report
    assert "ESPN summary endpoint down" in report["error"]
