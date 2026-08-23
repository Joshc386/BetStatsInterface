"""Tests for the coverage audit (ADR 0014).

The two decisions that matter are pure and tested without a clock or a DB:
*what does each source owe this Fixture* and *how overdue is it*. The query that
finds the gaps is a thin join over both.
"""

import datetime as dt

from ingestion.coverage import (
    KNOWN_GAP_AFTER,
    PLAYER_FBREF,
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


def test_covered_league_owes_both_sources():
    assert expected_sources("Premier League", "club_league", "E0") == frozenset(
        {TEAM_FDCOUK, PLAYER_FBREF}
    )
    assert expected_sources("Championship", "club_league", "E1") == frozenset(
        {TEAM_FDCOUK, PLAYER_FBREF}
    )


def test_lower_tiers_owe_team_data_but_not_player_data():
    """The single most important line in the audit. L1/L2 player data is out of
    scope by design — 6,649 finished Fixtures have no player rows and that is
    CORRECT. Treating them as gaps would bury the real signal ~400:1."""
    for comp, key in (("League One", "E2"), ("League Two", "E3")):
        owed = expected_sources(comp, "club_league", key)
        assert TEAM_FDCOUK in owed
        assert PLAYER_FBREF not in owed


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
