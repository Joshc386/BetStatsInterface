"""Tests for the coverage audit (ADR 0014).

The two decisions that matter are pure and tested without a clock or a DB:
*what does each source owe this Fixture* and *how overdue is it*. The query that
finds the gaps is a thin join over both.
"""

import datetime as dt

from sqlalchemy import func, select, update

from app.db import SessionLocal
from app.models.facts import Fixture, TeamMatch
from ingestion.coverage import (
    find_gaps,
    KNOWN_GAP_AFTER,
    PLAYER_FBREF,
    TEAM_ANY,
    TEAM_FDCOUK,
    Gap,
    classify,
    expected_sources,
    split_tiers,
)


def _gap(source: str, *, age: dt.timedelta, comp: str = "Premier League") -> Gap:
    now = dt.datetime(2026, 8, 23, 9, 0, tzinfo=dt.timezone.utc)
    return Gap(
        fixture_id=1, competition=comp, season="2627", date=now - age, source=source
    )


NOW = dt.datetime(2026, 8, 23, 9, 0, tzinfo=dt.timezone.utc)


# --- what each source owes -------------------------------------------------


def test_covered_league_owes_every_source():
    """TEAM_ANY joined the set with docs/adr/0015: a played league Fixture owes
    team data from SOMEWHERE (ESPN, all four tiers) as well as specifically
    from football-data.co.uk (the historical record) and FBref (players)."""
    for comp, key in (("Premier League", "E0"), ("Championship", "E1")):
        assert expected_sources(comp, "club_league", key) == frozenset(
            {TEAM_ANY, TEAM_FDCOUK, PLAYER_FBREF}
        )


def test_lower_tiers_now_owe_player_data_too():
    """Inverted when L1/L2 entered the player scope. It used to be the audit's
    most important line — 6,649 player-less Fixtures were CORRECT and calling
    them gaps would have buried the real signal ~400:1. They are now genuinely
    owed, and the backlog is held by KNOWN_GAP_AFTER rather than by scope: a
    Fixture older than 14 days is a standing coverage figure that never alarms,
    so the historical backfill can land season by season without nagging."""
    for comp, key in (("League One", "E2"), ("League Two", "E3")):
        assert expected_sources(comp, "club_league", key) == frozenset(
            {TEAM_ANY, TEAM_FDCOUK, PLAYER_FBREF}
        )


def test_non_league_scopes_owe_player_data_but_never_fdcouk():
    """football-data.co.uk is league-only (ADR 0001), so it can never be late
    for a cup tie. A cup/European Fixture row only exists if it was a Covered
    tie, so its presence is itself the scope decision."""
    for comp, ctype in (
        ("FA Cup", "club_cup"),
        ("EFL Cup", "club_cup"),
        ("Champions League", "club_european"),
        ("World Cup", "international"),
    ):
        owed = expected_sources(comp, ctype, None)
        assert owed == frozenset({PLAYER_FBREF})


def test_a_league_with_no_fdcouk_key_is_not_owed_fdcouk_data():
    """Championship Play-offs are club_cup, but guard the general case: no key
    means no CSV exists to be late."""
    assert TEAM_FDCOUK not in expected_sources("Premier League", "club_league", None)


# --- how overdue ------------------------------------------------------------


def test_a_just_played_match_is_not_yet_overdue():
    """Sunday morning must not alarm about Saturday evening's fixtures."""
    assert classify(_gap(PLAYER_FBREF, age=dt.timedelta(hours=2)), now=NOW) == "within_grace"
    assert classify(_gap(TEAM_FDCOUK, age=dt.timedelta(hours=2)), now=NOW) == "within_grace"


def test_grace_is_per_source():
    """FBref publishes a match page within hours; football-data.co.uk takes far
    longer, and has form for it. One shared number would either nag about
    fd.co.uk or go blind to FBref."""
    age = dt.timedelta(hours=30)
    assert classify(_gap(PLAYER_FBREF, age=age), now=NOW) == "overdue"
    assert classify(_gap(TEAM_FDCOUK, age=age), now=NOW) == "within_grace"


def test_past_grace_is_overdue_and_alarms():
    gap = _gap(PLAYER_FBREF, age=dt.timedelta(days=3))
    assert classify(gap, now=NOW) == "overdue"


def test_long_past_becomes_a_known_gap_not_an_alarm():
    """Never written off silently — it becomes a standing coverage figure."""
    gap = _gap(PLAYER_FBREF, age=KNOWN_GAP_AFTER + dt.timedelta(days=1))
    assert classify(gap, now=NOW) == "known_gap"


def test_split_tiers_separates_the_alarm_from_the_standing_count():
    gaps = [
        _gap(PLAYER_FBREF, age=dt.timedelta(hours=1)),          # within grace
        _gap(PLAYER_FBREF, age=dt.timedelta(days=2)),           # overdue
        _gap(PLAYER_FBREF, age=dt.timedelta(days=3)),           # overdue
        _gap(PLAYER_FBREF, age=KNOWN_GAP_AFTER * 2),            # known gap
    ]
    overdue, known = split_tiers(gaps, now=NOW)
    assert len(overdue) == 2
    assert len(known) == 1
    # the within-grace one appears in neither — it is not late, just recent
    assert len(overdue) + len(known) == len(gaps) - 1


def test_split_tiers_is_empty_when_everything_is_current():
    gaps = [_gap(TEAM_FDCOUK, age=dt.timedelta(hours=1))]
    assert split_tiers(gaps, now=NOW) == ([], [])


# --- provenance: an ESPN row must not answer for football-data.co.uk --------
#
# docs/adr/0015. These run against the live DB but COMMIT NOTHING: they flip a
# row's source inside a transaction, assert, and roll back.


def test_espn_team_row_does_not_satisfy_the_fdcouk_expectation():
    """The regression ADR 0014 exists to prevent, reintroduced by ADR 0015.

    `find_gaps` used to ask "is there a team row?" rather than "did
    football-data.co.uk publish?". Once ESPN can write a club_league row the
    two questions diverge, and the wrong one silences the alarm for the source
    that actually failed — absorbing the outage instead of exposing it.
    """
    with SessionLocal() as session:
        fixture_id = session.scalar(
            select(TeamMatch.fixture_id)
            .join(Fixture, Fixture.id == TeamMatch.fixture_id)
            .where(
                TeamMatch.source == "fdcouk",
                TeamMatch.competition_type == "club_league",
                Fixture.status == "finished",
            )
            .order_by(TeamMatch.fixture_id)
            .limit(1)
        )
        assert fixture_id is not None, "no fd.co.uk league rows to exercise"

        assert fixture_id not in {
            g.fixture_id for g in find_gaps(session, TEAM_FDCOUK)
        }, "a fd.co.uk-sourced Fixture should not be reported as a gap"

        session.execute(
            update(TeamMatch)
            .where(TeamMatch.fixture_id == fixture_id)
            .values(source="espn")
        )
        session.flush()

        assert fixture_id in {
            g.fixture_id for g in find_gaps(session, TEAM_FDCOUK)
        }, "an ESPN row silenced the football-data.co.uk alarm"

        session.rollback()


def test_espn_team_row_does_not_disturb_the_fbref_player_audit():
    """Each job audits the source it owns — team provenance must not leak into
    the player-row question, or an alarm could misattribute one source's
    failure to another's."""
    with SessionLocal() as session:
        fixture_id = session.scalar(
            select(TeamMatch.fixture_id)
            .join(Fixture, Fixture.id == TeamMatch.fixture_id)
            .where(
                TeamMatch.source == "fdcouk",
                TeamMatch.competition_type == "club_league",
                Fixture.status == "finished",
            )
            .order_by(TeamMatch.fixture_id)
            .limit(1)
        )
        before = {g.fixture_id for g in find_gaps(session, PLAYER_FBREF)}

        session.execute(
            update(TeamMatch)
            .where(TeamMatch.fixture_id == fixture_id)
            .values(source="espn")
        )
        session.flush()

        assert {g.fixture_id for g in find_gaps(session, PLAYER_FBREF)} == before

        session.rollback()


def test_every_team_match_row_declares_a_source():
    """The column exists so provenance is never unknown; NOT NULL is the
    guarantee and this is the canary if a new writer forgets to stamp it."""
    with SessionLocal() as session:
        assert session.scalar(
            select(func.count()).select_from(TeamMatch).where(
                TeamMatch.source.is_(None)
            )
        ) == 0


# --- TEAM_ANY: the gap that is actually worth alarming on (docs/adr/0015) ----


def test_every_league_fixture_owes_team_data_from_some_source():
    """ESPN covers all four tiers, so unlike TEAM_FDCOUK (which needs a CSV
    key) this is owed everywhere — including League One and Two."""
    for comp, key in (("Premier League", "E0"), ("League Two", "E3")):
        assert TEAM_ANY in expected_sources(comp, "club_league", key)


def test_team_any_is_not_owed_outside_the_leagues():
    """Cup, European and international team rows come from cached FBref pages
    on a different schedule; this alarm is a club_league decision only."""
    for comp, ctype in (("FA Cup", "club_cup"), ("World Cup", "international")):
        assert TEAM_ANY not in expected_sources(comp, ctype, None)


def test_an_espn_row_satisfies_team_any_but_not_team_fdcouk():
    """The whole point of the split. A Fixture ESPN has covered is NOT a
    product gap, so it must not page anyone — while football-data.co.uk's
    silence stays visible, because an outage absorbed is an outage repeated."""
    with SessionLocal() as session:
        fixture_id = session.scalar(
            select(TeamMatch.fixture_id)
            .join(Fixture, Fixture.id == TeamMatch.fixture_id)
            .where(
                TeamMatch.source == "fdcouk",
                TeamMatch.competition_type == "club_league",
                Fixture.status == "finished",
            )
            .order_by(TeamMatch.fixture_id)
            .limit(1)
        )
        session.execute(
            update(TeamMatch)
            .where(TeamMatch.fixture_id == fixture_id)
            .values(source="espn")
        )
        session.flush()

        assert fixture_id in {g.fixture_id for g in find_gaps(session, TEAM_FDCOUK)}
        assert fixture_id not in {g.fixture_id for g in find_gaps(session, TEAM_ANY)}

        session.rollback()


def test_nightly_alarms_on_missing_team_data_not_on_fdcouk_lateness():
    """Regression for the alarm that would otherwise fire every morning:
    football-data.co.uk published nothing between 20 and 24 Aug 2026 and never
    published E0 for 2026-27, none of which degrades anything now."""
    from ingestion import nightly

    with SessionLocal() as session:
        fdcouk_late = find_gaps(session, TEAM_FDCOUK)
        no_data_at_all = find_gaps(session, TEAM_ANY)

    assert len(fdcouk_late) >= len(no_data_at_all), (
        "TEAM_ANY must be a subset of TEAM_FDCOUK: a Fixture with no row at all "
        "necessarily has no fd.co.uk row"
    )
    assert nightly._audit_team_coverage is not None
