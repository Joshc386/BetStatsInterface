"""Tests for the failure digest that replaced the modal failure popup.

The popup had two faults. It BLOCKED, so Task Scheduler's Last Run Result read
"success" while a modal sat unanswered on the desktop — the log was the only
honest signal. And it fired per run: `upcoming` retries twice per slot across
five slots, so one standing fault became up to fifteen identical popups a day.
Six landed on 2026-08-27 for a cup tie that merely had not been drawn yet.

So the digest collapses identical failures and reports them once, on demand.
"""

import datetime as dt

from ingestion.digest import Run, parse_runs, summarise

NOW = dt.datetime(2026, 8, 27, 20, 0)

SAMPLE = """[27/08/2026  8:02:15.88] ingestion.upcoming start 
  Premier League: 59 events -> {'created': 3}
  EFL Cup: 2 covered tie(s) with an unresolved opponent - add ESPN_TEAM_ALIASES entries:
    2026-09-08 TBD Home v Leeds United: 'TBD Home' matched no team
[27/08/2026  8:02:24.47] exit code 1 
[27/08/2026  8:32:16.23] ingestion.upcoming start 
  EFL Cup: 2 covered tie(s) with an unresolved opponent - add ESPN_TEAM_ALIASES entries:
    2026-09-08 TBD Home v Leeds United: 'TBD Home' matched no team
[27/08/2026  8:32:25.10] exit code 1 
[27/08/2026  9:02:16.00] ingestion.upcoming start 
  Premier League: 59 events -> {'created': 0}
[27/08/2026  9:02:25.00] exit code 0 
"""


def test_parse_runs_pairs_each_start_with_its_exit_code():
    runs = parse_runs("upcoming", SAMPLE)
    assert [r.exit_code for r in runs] == [1, 1, 0]
    assert runs[0].started == dt.datetime(2026, 8, 27, 8, 2, 15)
    assert all(r.job == "upcoming" for r in runs)


def test_a_single_digit_hour_parses():
    """The .cmd writes ' 8:02' with a leading space, not '08:02'."""
    assert parse_runs("upcoming", SAMPLE)[0].started.hour == 8


def test_watchdog_start_lines_are_not_run_boundaries():
    """matchday's log carries '[watchdog] start attempt 1/25' inside a run —
    anchoring on the word 'start' would split one run into several."""
    text = (
        "[30/08/2026 18:43:30.53] ingestion.matchday start \n"
        "[watchdog] start attempt 1/25 (12 pending) -> backfill.log\n"
        "[watchdog] start attempt 2/25 (4 pending) -> backfill.log\n"
        "[30/08/2026 19:05:08.41] exit code 1 \n"
    )
    runs = parse_runs("matchday", text)
    assert len(runs) == 1 and runs[0].exit_code == 1


def test_summarise_collapses_identical_failures_into_one_line():
    """The whole point: six identical popups become one line with a count."""
    out = summarise(parse_runs("upcoming", SAMPLE), since=NOW - dt.timedelta(days=1))
    assert "upcoming" in out
    assert "2 failed" in out
    assert out.count("unresolved opponent") == 1     # collapsed, not repeated


def test_summarise_says_so_when_nothing_failed():
    clean = [Run("nightly", NOW - dt.timedelta(hours=2), 0, [])]
    assert "no failures" in summarise(clean, since=NOW - dt.timedelta(days=1)).lower()


def test_runs_outside_the_window_are_ignored():
    old = parse_runs("upcoming", SAMPLE)
    out = summarise(old, since=dt.datetime(2026, 8, 28))   # after the sample
    assert "no failures" in out.lower()


def test_a_successful_coverage_line_is_not_reported_as_a_cause():
    """'nothing overdue' is the coverage audit reporting SUCCESS. It contains
    the word 'overdue', so a naive filter puts it at the top of every matchday
    entry — the digest then leads with good news dressed as a fault."""
    run = Run("matchday", NOW, 1, [
        "[coverage] FBref player rows: nothing overdue",
        "[watchdog] clean exit, no progress (4 still pending)",
    ])
    out = summarise([run], since=NOW - dt.timedelta(days=1))
    assert "nothing overdue" not in out
    assert "4 still pending" in out


def test_traceback_scaffolding_is_dropped_but_the_exception_survives():
    run = Run("nightly", NOW, 1, [
        "Traceback (most recent call last):",
        "    raise RuntimeError(",
        "(Background on this error at: https://sqlalche.me/e/20/gkpj)",
        "RuntimeError: football-data.co.uk returned nothing",
    ])
    out = summarise([run], since=NOW - dt.timedelta(days=1))
    assert "RuntimeError: football-data.co.uk returned nothing" in out
    assert "sqlalche.me" not in out
    assert "most recent call last" not in out


def test_routine_counter_lines_never_outrank_the_real_cause():
    """'skipped_finished' is a normal counter, but the SKIPPED pattern matches
    it case-insensitively. Unfiltered, six competitions' worth of healthy output
    outranked the one line that explained the failure."""
    run = Run("upcoming", NOW, 1, [
        "  League One: 120 events -> {'created': 0, 'skipped_finished': 24}",
        "  League Two: 120 events -> {'created': 0, 'skipped_finished': 24}",
        "  espn team rows -> {'written': 0, 'skipped_fdcouk': 0}",
        "  EFL Cup: 2 covered tie(s) with an unresolved opponent",
    ])
    out = summarise([run], since=NOW - dt.timedelta(days=1))
    assert "skipped_finished" not in out
    assert "unresolved opponent" in out
