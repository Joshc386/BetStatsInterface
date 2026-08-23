"""Tests for the unattended nightly refresh (Phase 5, tier 1).

Both steps hit the network, so we monkeypatch them and assert the orchestration
contract: it targets the *current* season and it *commits* points adjustments
(apply=True) — the two things a silent regression could get wrong.

The skip *verdict* is pure (the DB supplies first-kickoff facts, the function
decides), so it is tested directly — same split as `matchday.plan_competitions`.
"""

import datetime as dt

import pytest

from ingestion import coverage, nightly


def _no_gaps(now, log):
    """A coverage audit that finds nothing.

    The real one reads the live DB, so without this a unit test's verdict would
    depend on production data — the exact class of false signal ADR 0014 exists
    to remove. Tests that care about overdue data inject their own.
    """
    return []


def test_nightly_refreshes_current_season_and_applies_points(monkeypatch):
    calls: dict = {}

    def fake_team_ingest(seasons):
        calls["team_seasons"] = seasons
        return {"per_league_season": {f"Premier League {seasons[0]}": 10},
                "skipped": [], "fixtures": 10, "team_match": 20}

    def fake_points(*, apply, log=print):
        calls["points_apply"] = apply
        return [{"team": "Everton", "points": -8}]

    monkeypatch.setattr(nightly.team_match, "ingest", fake_team_ingest)
    monkeypatch.setattr(
        nightly.points_adjustments, "ingest_points_adjustments", fake_points
    )

    # A February 2027 run belongs to season 2627.
    result = nightly.run_nightly(
        now=dt.datetime(2027, 2, 1, tzinfo=dt.timezone.utc),
        log=lambda *a, **k: None,
        audit=_no_gaps,
    )

    assert calls["team_seasons"] == ["2627"]  # current season only, not a full backfill
    assert calls["points_apply"] is True  # writes, never a dry run
    assert result["season"] == "2627"
    assert result["points"] == [{"team": "Everton", "points": -8}]


# --- skip verdict: "pre-season" vs "fd.co.uk is broken" ------------------------
#
# football-data.co.uk publishes no CSV until a league has played, so a 404 is
# normal in August and alarming in September. The only thing that tells the two
# apart is whether the league has actually kicked off — which the ESPN-sourced
# `fixtures` table already knows.

NOW = dt.datetime(2026, 9, 1, 7, 0, tzinfo=dt.timezone.utc)
SKIP_E1 = "E1/2627: HTTPError: HTTP Error 404: Not Found"


def test_skip_is_expected_while_the_league_has_not_kicked_off():
    """August: no fixture has been played, so a 404 is the normal pre-season state."""
    # Season not started at all — no entry for the league.
    assert nightly.unexpected_skips([SKIP_E1], {}, NOW) == []
    # First fixture is still in the future.
    upcoming = {"E1/2627": NOW + dt.timedelta(days=13)}
    assert nightly.unexpected_skips([SKIP_E1], upcoming, NOW) == []


def test_skip_is_a_real_failure_once_the_league_has_played():
    """A league that kicked off days ago and still 404s is broken, not pre-season."""
    played = {"E1/2627": NOW - dt.timedelta(days=3)}
    assert nightly.unexpected_skips([SKIP_E1], played, NOW) == [SKIP_E1]


def test_grace_period_covers_the_publishing_lag_on_matchday():
    """fd.co.uk publishes soon after kickoff but not instantly — a match that
    kicked off hours ago must not raise the alarm on the same match day."""
    just_kicked_off = {"E1/2627": NOW - dt.timedelta(hours=2)}
    assert nightly.unexpected_skips([SKIP_E1], just_kicked_off, NOW) == []
    # ...but the grace window does expire.
    stale = {"E1/2627": NOW - nightly.PUBLISH_GRACE - dt.timedelta(hours=1)}
    assert nightly.unexpected_skips([SKIP_E1], stale, NOW) == [SKIP_E1]


def test_verdict_is_per_league_season_not_all_or_nothing():
    """One broken league must not be excused by another league being pre-season."""
    skips = [SKIP_E1, "E0/2627: HTTPError: HTTP Error 404: Not Found"]
    kickoffs = {
        "E1/2627": NOW - dt.timedelta(days=3),  # playing -> broken
        "E0/2627": NOW + dt.timedelta(days=6),  # not yet -> fine
    }
    assert nightly.unexpected_skips(skips, kickoffs, NOW) == [SKIP_E1]


def test_nightly_raises_when_a_started_league_was_skipped(monkeypatch):
    """The alarm must reach Task Scheduler (non-zero exit -> MessageBox), and only
    after both refresh steps have run — a dead source must not block points."""
    calls: dict = {}

    def fake_team_ingest(seasons):
        return {"per_league_season": {}, "skipped": [SKIP_E1],
                "fixtures": 0, "team_match": 0}

    def fake_points(*, apply, log=print):
        calls["points_ran"] = True
        return []

    monkeypatch.setattr(nightly.team_match, "ingest", fake_team_ingest)
    monkeypatch.setattr(
        nightly.points_adjustments, "ingest_points_adjustments", fake_points
    )
    monkeypatch.setattr(
        nightly, "first_kickoffs", lambda season, now: {"E1/2627": NOW - dt.timedelta(days=3)}
    )

    with pytest.raises(RuntimeError, match="E1/2627"):
        nightly.run_nightly(now=NOW, log=lambda *a, **k: None, audit=_no_gaps)

    assert calls["points_ran"] is True  # points still refreshed before the alarm


def test_nightly_stays_green_through_a_pre_season_skip(monkeypatch):
    """Today's real state: four 404s, nothing kicked off — must exit 0."""
    monkeypatch.setattr(
        nightly.team_match,
        "ingest",
        lambda seasons: {"per_league_season": {}, "skipped": [SKIP_E1],
                         "fixtures": 0, "team_match": 0},
    )
    monkeypatch.setattr(
        nightly.points_adjustments, "ingest_points_adjustments",
        lambda *, apply, log=print: [],
    )
    monkeypatch.setattr(nightly, "first_kickoffs", lambda season, now: {})

    result = nightly.run_nightly(now=NOW, log=lambda *a, **k: None, audit=_no_gaps)
    assert result["season"] == "2627"


# --- a dead ESPN degrades the run, it does not end it --------------------------
#
# 2026-08-05/06: ESPN's edge started 403ing the User-Agent both ESPN callers sent,
# the error propagated out of the points step, and the nightly died. Two things
# were wrong with that. The deductions are an enrichment of the computed table,
# not the league data this job exists to refresh; and raising there jumped the
# football-data.co.uk verdict below it, so a broken Facts source would have been
# hidden behind a broken footnote source.


def test_nightly_survives_a_dead_points_source(monkeypatch):
    """A 403 from ESPN leaves points empty and the run green."""
    import urllib.error

    def fake_points(*, apply, log=print):
        raise urllib.error.HTTPError(
            "https://site.api.espn.com/...", 403, "Forbidden", {}, None
        )

    monkeypatch.setattr(
        nightly.team_match,
        "ingest",
        lambda seasons: {"per_league_season": {"Premier League 2627": 10},
                         "skipped": [], "fixtures": 10, "team_match": 20},
    )
    monkeypatch.setattr(
        nightly.points_adjustments, "ingest_points_adjustments", fake_points
    )
    monkeypatch.setattr(nightly, "first_kickoffs", lambda season, now: {})

    lines: list[str] = []
    result = nightly.run_nightly(now=NOW, log=lines.append, audit=_no_gaps)

    assert result["points"] == []  # empty, not absent — callers keep their contract
    assert result["team"]["fixtures"] == 10  # the job's actual work still landed
    assert any("DEGRADED" in ln and "403" in ln for ln in lines), (
        "a degraded source must be visible in the log, not silent"
    )


def test_a_dead_points_source_does_not_mask_the_fdcouk_alarm(monkeypatch):
    """The regression that made this worse than a crash.

    ESPN failing must not stop the football-data.co.uk verdict from firing — that
    verdict is the only thing that tells "pre-season" from "Facts source broken".
    """
    def fake_points(*, apply, log=print):
        raise RuntimeError("ESPN is down")

    monkeypatch.setattr(
        nightly.team_match,
        "ingest",
        lambda seasons: {"per_league_season": {}, "skipped": [SKIP_E1],
                         "fixtures": 0, "team_match": 0},
    )
    monkeypatch.setattr(
        nightly.points_adjustments, "ingest_points_adjustments", fake_points
    )
    monkeypatch.setattr(
        nightly, "first_kickoffs",
        lambda season, now: {"E1/2627": NOW - dt.timedelta(days=3)},
    )

    with pytest.raises(RuntimeError, match="E1/2627"):
        nightly.run_nightly(now=NOW, log=lambda *a, **k: None, audit=_no_gaps)


def test_nightly_alarms_when_fdcouk_is_behind_on_individual_fixtures(monkeypatch):
    """Partial publication is the case the fetch check cannot see (ADR 0014).

    football-data.co.uk published 12 of 23 Championship games on 2026-08-23.
    Nothing was "skipped" — the CSV existed and parsed — so unexpected_skips had
    nothing to report, and the other 11 fixtures were silently missing.
    """
    monkeypatch.setattr(
        nightly.team_match,
        "ingest",
        lambda seasons: {"per_league_season": {"E1/2627": 12}, "skipped": []},
    )
    monkeypatch.setattr(
        nightly.points_adjustments, "ingest_points_adjustments", lambda **k: []
    )

    late = [
        coverage.Gap(
            fixture_id=7,
            competition="Championship",
            season="2627",
            date=NOW - dt.timedelta(days=3),
            source=coverage.TEAM_FDCOUK,
        )
    ]
    with pytest.raises(RuntimeError, match="has not published"):
        nightly.run_nightly(
            now=NOW, log=lambda *a, **k: None, audit=lambda now, log: late
        )


def test_a_clean_fetch_with_no_gaps_stays_green(monkeypatch):
    """The alarm must not fire just because the audit ran."""
    monkeypatch.setattr(
        nightly.team_match,
        "ingest",
        lambda seasons: {"per_league_season": {"E1/2627": 12}, "skipped": []},
    )
    monkeypatch.setattr(
        nightly.points_adjustments, "ingest_points_adjustments", lambda **k: []
    )
    result = nightly.run_nightly(now=NOW, log=lambda *a, **k: None, audit=_no_gaps)
    assert result["overdue"] == []
