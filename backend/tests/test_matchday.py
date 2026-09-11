"""Tests for the supervised match-day player-refresh planner (Phase 5, tier 2).

The run itself is I/O (spawns the FBref watchdog), but the *decision* of which
competitions to refresh, in what order, is pure — that's what these lock down.
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.reference import Competition
from ingestion import coverage, matchday, run_backfill
from ingestion.matchday import (
    ALL_PLAYER_COMPETITIONS,
    CUP_PLAYER_COMPETITIONS,
    LEAGUE_PLAYER_COMPETITIONS,
    cup_pending,
    plan_competitions,
)


def test_default_plans_only_pending_leagues_in_canonical_order():
    """No explicit request -> the leagues that have pending player work, in the
    fixed tier order (never the caller's set order)."""
    assert plan_competitions(None, pending_leagues={"Championship", "Premier League"}) == [
        "Premier League",
        "Championship",
    ]
    assert plan_competitions(None, pending_leagues={"Championship"}) == ["Championship"]
    assert plan_competitions(None, pending_leagues=set()) == []


def test_lower_tiers_plan_after_the_top_two():
    """League One / Two joined the player scope once the spike measured 100%
    population on Sh/SoT/TklW/Fls/Min across all six seasons. Order is by tier,
    so a matchday that runs short still gets the highest-value data first."""
    assert plan_competitions(
        None,
        pending_leagues={"League Two", "League One", "Championship", "Premier League"},
    ) == ["Premier League", "Championship", "League One", "League Two"]
    assert plan_competitions(None, pending_leagues={"League Two"}) == ["League Two"]


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
    assert LEAGUE_PLAYER_COMPETITIONS == [
        "Premier League",
        "Championship",
        "League One",
        "League Two",
    ]


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

    The coverage audit (ADR 0014) is stubbed for the same reason in miniature:
    it reads the live DB, so leaving it in would let production data decide a
    unit test's outcome. Tests that care about it inject their own.
    """
    ran: list[str] = []
    monkeypatch.setattr(matchday, "_audit_player_coverage", lambda now, log: [])
    monkeypatch.setattr(matchday.run_backfill, "_pending", _fake_pending(pending))
    monkeypatch.setattr(matchday, "espn_pending", lambda season, log=print: espn or {})
    monkeypatch.setattr(
        matchday.run_backfill, "run", lambda season, comp: ran.append(comp) or 0
    )
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


def test_an_empty_plan_still_audits_and_reports_overdue_data(monkeypatch):
    """The regression test for ADR 0014's failure.

    On four consecutive mornings matchday logged "no pending player data" and
    exited 0 while 6 Premier League and 11 Championship fixtures sat un-ingested.
    The pending probe counts only FINISHED fixtures, and nothing had marked them
    finished, so an empty plan was a claim nobody checked. Now the audit checks
    it: no pending work is only success if the data agrees.
    """
    _isolate(monkeypatch, {})
    late = [
        coverage.Gap(
            fixture_id=1,
            competition="Premier League",
            season="2627",
            date=dt.datetime(2026, 8, 21, 19, 0, tzinfo=dt.timezone.utc),
            source=coverage.PLAYER_FBREF,
        )
    ]

    report = matchday.run_matchday(
        season="2627", log=lambda *a, **k: None, audit=lambda now, log: late
    )

    assert report["ran"] == []          # still nothing it could run
    assert report["overdue"] == late    # but it no longer claims all is well


def test_a_genuinely_quiet_day_reports_nothing_overdue(monkeypatch):
    """A real quiet day must stay quiet — the audit must not manufacture noise."""
    _isolate(monkeypatch, {})
    report = matchday.run_matchday(season="2627", log=lambda *a, **k: None)
    assert report["ran"] == []
    assert report["overdue"] == []


# --- promotion play-offs (ADR 0017) -----------------------------------------


def test_pending_counts_the_playoff_sibling_alongside_its_league():
    """The trigger has to agree with the ingester. `backfill_season` has spanned
    the league AND its play-off competition since ADR 0004, but `_pending` — the
    probe that decides whether it ever runs — counted the league alone, so a
    play-off round that landed after the final league game was invisible."""
    with SessionLocal() as session:
        ids = matchday.run_backfill._competition_ids(session, "Championship")
        names = set(
            session.scalars(
                select(Competition.name).where(Competition.id.in_(ids))
            )
        )
    assert names == {"Championship", "Championship Play-offs"}


def test_pending_is_unchanged_for_a_league_with_no_playoffs():
    """The Premier League is the case that proves the lookup is data-driven:
    no seeded sibling, no second competition, no behaviour change."""
    with SessionLocal() as session:
        ids = matchday.run_backfill._competition_ids(session, "Premier League")
    assert len(ids) == 1


def test_pending_is_unchanged_for_a_cup():
    """No cup has a '<name> Play-offs' sibling, so the cup probe is untouched."""
    with SessionLocal() as session:
        ids = matchday.run_backfill._competition_ids(session, "FA Cup")
    assert len(ids) == 1
def _no_ingest(monkeypatch, code: int = 0):
    """Stub out everything `run` does except the team-row pass."""
    built: list[str] = []
    monkeypatch.setattr(run_backfill, "_set_sleep_blocked", lambda on: None)
    monkeypatch.setattr(run_backfill, "_run", lambda season, comp: code)
    monkeypatch.setattr(
        run_backfill,
        "_run_schedule_driven",
        lambda season, comp, module, count_fn, kind: code,
    )
    monkeypatch.setattr(
        run_backfill.cups,
        "backfill_cup_team_match",
        lambda season, cup_name, log=print: built.append(cup_name),
    )
    return built


def test_a_league_backfill_builds_its_playoff_team_rows(monkeypatch):
    built = _no_ingest(monkeypatch)
    assert run_backfill.run("2526", "League One") == 0
    assert built == ["League One Play-offs"]


def test_a_league_with_no_playoffs_builds_nothing(monkeypatch):
    built = _no_ingest(monkeypatch)
    run_backfill.run("2526", "Premier League")
    assert built == []


def test_a_cup_backfill_builds_its_own_team_rows(monkeypatch):
    built = _no_ingest(monkeypatch)
    run_backfill.run("2425", "FA Cup")
    assert built == ["FA Cup"]


def test_a_completed_stage_still_builds_missing_team_rows(monkeypatch):
    """`_run` returns 0 immediately when nothing is pending. The pass must still
    happen on that path — it is what lets a re-run repair a backlog of team rows
    whose player data already landed, with no re-fetch."""
    built = _no_ingest(monkeypatch)
    assert run_backfill.run("2425", "League One") == 0
    assert built == ["League One Play-offs"]


def test_team_rows_are_built_even_when_the_watchdog_gave_up(monkeypatch):
    """A partial ingest still cached pages worth turning into rows, and the pass
    skips any fixture with no cached page. Failing the run must not also discard
    the rows it did earn."""
    built = _no_ingest(monkeypatch, code=1)
    assert run_backfill.run("2526", "League One") == 1
    assert built == ["League One Play-offs"]


def test_an_international_selector_builds_nothing():
    """Internationals get their team rows from `ingestion.internationals team`,
    never the cups pass — and the selector ('WC Qual UEFA') is not a stored
    competition name at all, so nothing matches. Guards against the `season`
    argument, which on that path is a FETCH EDITION rather than a stored season,
    reaching a writer that would take it literally."""
    assert run_backfill._team_row_competitions("WC Qual UEFA") == []
    assert run_backfill._team_row_competitions("World Cup Qualifiers") == []


def test_team_row_competitions_is_decided_by_competition_type():
    """The rule is the TYPE, not a hardcoded list: cup-shaped competitions get
    rows from the cached match pages, league-shaped ones get theirs from
    football-data.co.uk (ADR 0001) and must never be built here."""
    assert run_backfill._team_row_competitions("League One") == ["League One Play-offs"]
    assert run_backfill._team_row_competitions("Championship") == [
        "Championship Play-offs"
    ]
    assert run_backfill._team_row_competitions("FA Cup") == ["FA Cup"]
    assert run_backfill._team_row_competitions("Premier League") == []
