"""Tests for the supervised match-day player-refresh planner (Phase 5, tier 2).

The run itself is I/O (spawns the FBref watchdog), but the *decision* of which
competitions to refresh, in what order, is pure — that's what these lock down.
"""

import pytest

from ingestion import matchday
from ingestion.matchday import (
    ALL_PLAYER_COMPETITIONS,
    CUP_PLAYER_COMPETITIONS,
    LEAGUE_PLAYER_COMPETITIONS,
    manual_pending,
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


# --- surfacing the work the unattended run will NOT do -------------------------
#
# The scheduled task runs with no args, so it only ever ingests the two leagues.
# After a cup round or a European night the player data sits there unfetched and
# the log reads exactly like a quiet day — so the run has to say so.


def _fake_pending(mapping: dict[str, int]):
    return lambda season, competition_name: mapping.get(competition_name, 0)


def test_manual_pending_reports_only_the_cups_the_default_run_skips(monkeypatch):
    monkeypatch.setattr(
        matchday.run_backfill,
        "_pending",
        _fake_pending({"FA Cup": 32, "Champions League": 8, "Premier League": 10}),
    )
    # Leagues are excluded: the default run ingests those itself.
    assert manual_pending("2627") == {"FA Cup": 32, "Champions League": 8}


def test_manual_pending_is_quiet_when_the_cups_are_up_to_date(monkeypatch):
    monkeypatch.setattr(matchday.run_backfill, "_pending", _fake_pending({}))
    assert manual_pending("2627") == {}


def test_quiet_day_still_reports_pending_cup_work(monkeypatch):
    """The case this exists for: no league work, so the run would otherwise log
    'nothing to do' and exit while an FA Cup round sits unfetched."""
    monkeypatch.setattr(
        matchday.run_backfill, "_pending", _fake_pending({"FA Cup": 32})
    )
    lines: list[str] = []
    report = matchday.run_matchday(season="2627", log=lines.append)

    assert report["ran"] == []  # nothing ingested automatically — unchanged
    assert report["pending_manual"] == {"FA Cup": 32}
    blob = "\n".join(lines)
    assert "FA Cup 32" in blob
    assert "ingestion.matchday" in blob  # tells you the command to run


def test_no_pending_line_when_there_is_nothing_to_chase(monkeypatch):
    monkeypatch.setattr(matchday.run_backfill, "_pending", _fake_pending({}))
    lines: list[str] = []
    report = matchday.run_matchday(season="2627", log=lines.append)

    assert report["pending_manual"] == {}
    assert "MANUAL" not in "\n".join(lines)
