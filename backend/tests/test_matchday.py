"""Tests for the supervised match-day player-refresh planner (Phase 5, tier 2).

The run itself is I/O (spawns the FBref watchdog), but the *decision* of which
competitions to refresh, in what order, is pure — that's what these lock down.
"""

import pytest

from ingestion.matchday import (
    ALL_PLAYER_COMPETITIONS,
    CUP_PLAYER_COMPETITIONS,
    LEAGUE_PLAYER_COMPETITIONS,
    plan_competitions,
)


def test_default_plans_only_pending_leagues_in_canonical_order():
    """No explicit request -> the leagues that have pending player work, in the
    fixed PL-then-Championship order (never the caller's set order)."""
    assert plan_competitions(None, pending_leagues={"Championship", "Premier League"}) == [
        "Premier League",
        "Championship",
    ]
    assert plan_competitions(None, pending_leagues={"Championship"}) == ["Championship"]
    assert plan_competitions(None, pending_leagues=set()) == []


def test_explicit_request_is_validated_and_order_preserved():
    """Named competitions run exactly as asked (a cup evening), order kept."""
    req = ["FA Cup", "Champions League"]
    assert plan_competitions(req, pending_leagues=set()) == req


def test_unsupported_competition_fails_loud():
    """UEFA Super Cup is deferred (soccerdata crashes on its schedule) and a typo
    is a mistake — both must raise, not silently drop."""
    with pytest.raises(ValueError, match="UEFA Super Cup"):
        plan_competitions(["UEFA Super Cup"], pending_leagues=set())
    with pytest.raises(ValueError, match="Prem League"):
        plan_competitions(["Prem League"], pending_leagues=set())


def test_competition_sets_are_consistent():
    """The player competitions are exactly the league + cup/European sets, and the
    deferred UEFA Super Cup is not among them."""
    assert ALL_PLAYER_COMPETITIONS == LEAGUE_PLAYER_COMPETITIONS + CUP_PLAYER_COMPETITIONS
    assert "UEFA Super Cup" not in ALL_PLAYER_COMPETITIONS
    assert LEAGUE_PLAYER_COMPETITIONS == ["Premier League", "Championship"]
