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
    cup_pending,
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
    assert cup_pending("2627") == {"FA Cup": 32, "Champions League": 8}


def test_manual_pending_is_quiet_when_the_cups_are_up_to_date(monkeypatch):
    monkeypatch.setattr(matchday.run_backfill, "_pending", _fake_pending({}))
    assert cup_pending("2627") == {}


def _isolate(monkeypatch, pending: dict[str, int], *, espn: dict | None = None):
    """Run matchday with no network and no spawned backfill.

    Both are real in this module: espn_pending reads ESPN live, and a planned
    competition spawns the FBref watchdog as a subprocess. An early version of
    these tests did exactly that and ingested two FA Cup ties for real.
    """
    ran: list[str] = []
    monkeypatch.setattr(matchday.run_backfill, "_pending", _fake_pending(pending))
    monkeypatch.setattr(matchday, "espn_pending", lambda season, log=print: espn or {})
    monkeypatch.setattr(
        matchday.run_backfill, "run", lambda season, comp: ran.append(comp) or 0
    )
    monkeypatch.setattr(matchday.cups, "backfill_cup_team_match", lambda *a, **k: None)
    monkeypatch.setattr(matchday, "_sweep_orphans", lambda log=print: None)
    return ran


def test_a_played_cup_round_is_now_ingested_not_merely_reported(monkeypatch):
    """The behaviour ADR 0012 changes. Before, a quiet league day logged
    'nothing to do' and exited while an FA Cup round sat unfetched - and the
    line that was supposed to warn about it could never fire."""
    ran = _isolate(monkeypatch, {"FA Cup": 32})
    lines: list[str] = []

    report = matchday.run_matchday(season="2627", log=lines.append)

    assert report["ran"] == ["FA Cup"]
    assert ran == ["FA Cup"]


def test_european_signal_puts_a_competition_into_the_plan(monkeypatch):
    """Europe has no fixture rows to probe, so ESPN is the only thing that can
    say a covered club played a tie we do not hold."""
    ran = _isolate(monkeypatch, {}, espn={"Champions League": 6})
    lines: list[str] = []

    report = matchday.run_matchday(season="2627", log=lines.append)

    assert report["ran"] == ["Champions League"]
    assert ran == ["Champions League"]


def test_work_still_outstanding_after_the_run_is_reported(monkeypatch):
    """A competition still pending once its run finished is a failure, not a
    quiet day - it gets named, with the command to chase it."""
    _isolate(monkeypatch, {"FA Cup": 32})
    lines: list[str] = []

    report = matchday.run_matchday(season="2627", log=lines.append)

    assert report["pending_manual"] == {"FA Cup": 32}
    blob = "\n".join(lines)
    assert "STILL PENDING" in blob and "FA Cup 32" in blob
    assert "ingestion.matchday" in blob


def test_nothing_pending_runs_nothing_and_says_nothing(monkeypatch):
    ran = _isolate(monkeypatch, {})
    lines: list[str] = []

    report = matchday.run_matchday(season="2627", log=lines.append)

    assert report["ran"] == [] and ran == []
    assert report["pending_manual"] == {}
    assert "STILL PENDING" not in "\n".join(lines)


# --- cups and Europe now ride the unattended run (ADR 0012) ----------------


def test_default_plan_includes_pending_cups_after_the_leagues():
    """Leagues first (the frequent case), then any cup/European competition with
    pending work - all in canonical order, never the caller's set order."""
    plan = plan_competitions(
        None,
        pending_leagues={"Championship"},
        pending_cups={"Champions League", "EFL Cup"},
    )
    assert plan == ["Championship", "EFL Cup", "Champions League"]


def test_default_plan_runs_cups_even_with_no_league_work():
    """A midweek European night with no league fixtures still has work to do -
    the old planner returned [] here and the round was silently never fetched."""
    assert plan_competitions(
        None, pending_leagues=set(), pending_cups={"FA Cup"}
    ) == ["FA Cup"]


def test_default_plan_is_empty_when_nothing_is_pending():
    assert plan_competitions(None, pending_leagues=set(), pending_cups=set()) == []
