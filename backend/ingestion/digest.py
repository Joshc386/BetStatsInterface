"""Failure digest for the four scheduled jobs — replaces the modal popup.

`notify_failure.ps1` showed a WinForms MessageBox on any non-zero exit. Two
problems. It BLOCKED, so `cmd.exe` did not return until someone clicked OK and
Task Scheduler's Last Run Result read "success" while the job had plainly
failed. And it fired PER RUN: `upcoming` retries twice per slot across five
slots, so a single standing fault became up to fifteen identical popups a day —
six landed on 2026-08-27 for an EFL Cup tie that had simply not been drawn yet.

This reports the same information on demand instead, collapsing identical
failures so a standing fault reads as one line with a count. Reading the logs is
enough: they already carry `[dd/mm/yyyy HH:MM:SS.ff] <job> start` and
`... exit code N` markers, so nothing new has to be recorded.

    python -m ingestion.digest          # last 24h
    python -m ingestion.digest 72       # last 72h

Always exits 0 — it is a report, not an alarm.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

JOBS = ("nightly", "upcoming", "matchday", "squads")
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# Exit code for a run whose `exit code` line never arrived. NOT the same as
# "still running": it is only assigned once a LATER run has started in the same
# log, which proves the earlier one is over.
#
# Without it such a run vanished — the next `start` reset the parser, so it
# counted as neither a failure nor a run checked, and the digest reported
# "no failures" over the top of it. Six had already gone that way, three of them
# (upcoming, 19:30 on 01-03/09/2026) inside the run of clean days this was
# trusted to be reporting on. A job killed mid-flight — machine slept, task
# timed out, Ctrl-C — is exactly the failure nobody is watching for, so it is
# the one the digest must not drop.
NO_EXIT_LINE = -1

_KILLED = "run ended with no exit code — killed mid-flight (machine slept? task timed out?)"

# Anchored on the full timestamp, NOT the word "start": matchday's log carries
# "[watchdog] start attempt 1/25" inside a run, which would otherwise split one
# run into several. The hour may be space-padded (" 8:02", not "08:02").
_START = re.compile(r"^\[(\d{2}/\d{2}/\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})[.\d]*\]\s+\S+\s+start")
_EXIT = re.compile(r"^\[(\d{2}/\d{2}/\d{4})\s+[\d: .]+\]\s+exit code (\d+)")

# Lines worth showing for a failed run. Deliberately narrow: a digest that
# reprints whole runs is a log, and nobody reads a log they already have.
_NOTABLE = re.compile(
    r"unresolved|matched no team|Error|ERROR|FAILED|"
    r"STILL PENDING|unexpected|overdue|stalled|SKIPPED",
    re.IGNORECASE,
)

# Lines the pattern above catches but which carry nothing. "nothing overdue" is
# the coverage audit reporting SUCCESS and would otherwise head every matchday
# entry; the rest is traceback scaffolding whose informative line — the actual
# exception — is matched on its own merits.
_NOISE = re.compile(
    r"nothing overdue|Background on this error|^\s*raise \w+\($|"
    r"^\s*Traceback \(most recent call last\):$|"
    # Any routine counter line — the jobs report progress as "label -> {dict}".
    # Those dicts carry 'skipped_finished' / 'skipped_fdcouk', which the
    # case-insensitive SKIPPED above matches, so without this every upcoming
    # entry led with healthy output and buried the actual cause. Matching the
    # SHAPE rather than each label avoids re-fixing this per new counter.
    r"-> \{.*\}",
)


@dataclass(frozen=True)
class Run:
    """One invocation: when it started, how it ended, and what it printed."""

    job: str
    started: dt.datetime
    exit_code: int
    lines: list[str]


def parse_runs(job: str, text: str) -> list[Run]:
    """Every run in one log.

    A start line with no exit line means one of two things, told apart by what
    follows it. If a LATER run has started, the earlier one is over and never
    reported how it ended: that is a failure (`NO_EXIT_LINE`), not an absence.
    If nothing follows, it is the trailing entry and may still be in flight, so
    it is omitted — it has not failed yet.
    """
    runs: list[Run] = []
    started: dt.datetime | None = None
    body: list[str] = []
    for line in text.splitlines():
        if (m := _START.match(line)) is not None:
            if started is not None:
                runs.append(Run(job, started, NO_EXIT_LINE, body))
            day, hh, mm, ss = m.groups()
            started = dt.datetime.strptime(day, "%d/%m/%Y").replace(
                hour=int(hh), minute=int(mm), second=int(ss)
            )
            body = []
            continue
        if (m := _EXIT.match(line)) is not None and started is not None:
            runs.append(Run(job, started, int(m.group(2)), body))
            started, body = None, []
            continue
        if started is not None:
            body.append(line.rstrip())
    return runs


def summarise(runs: list[Run], *, since: dt.datetime) -> str:
    """One block per job that failed in the window, identical causes collapsed."""
    recent = [r for r in runs if r.started >= since]
    failed = [r for r in recent if r.exit_code != 0]
    if not failed:
        return (
            f"no failures since {since:%d/%m %H:%M} "
            f"({len(recent)} run(s) checked)"
        )

    out: list[str] = []
    for job in JOBS:
        job_failed = [r for r in failed if r.job == job]
        if not job_failed:
            continue
        job_runs = [r for r in recent if r.job == job]
        out.append(f"{job}: {len(job_failed)} failed of {len(job_runs)} run(s)")
        out.append(
            f"  first {min(r.started for r in job_failed):%d/%m %H:%M}"
            f"  last {max(r.started for r in job_failed):%d/%m %H:%M}"
        )
        causes = Counter(
            line.strip()
            for run in job_failed
            for line in run.lines
            if _NOTABLE.search(line) and not _NOISE.search(line)
        )
        # A killed run's body is ordinary healthy output — it died before it
        # could complain — so nothing in it matches _NOTABLE and it would
        # otherwise fall through to "(no error line matched)", which is true but
        # says nothing about what happened. Name the shape instead.
        if killed := sum(1 for r in job_failed if r.exit_code == NO_EXIT_LINE):
            causes[_KILLED] = killed
        for cause, n in causes.most_common(5):
            out.append(f"  {n}x {cause}")
        if not causes:
            out.append("  (no error line matched — read the log)")
    return "\n".join(out)


def build(hours: int = 24, *, now: dt.datetime | None = None) -> str:
    """The digest across all four job logs."""
    now = now or dt.datetime.now()
    since = now - dt.timedelta(hours=hours)
    runs: list[Run] = []
    for job in JOBS:
        log = LOG_DIR / f"{job}.log"
        if log.exists():
            runs += parse_runs(job, log.read_text(encoding="utf-8", errors="replace"))
    header = f"BetStats job digest — {now:%d/%m/%Y %H:%M}, last {hours}h"
    return f"{header}\n{'-' * len(header)}\n{summarise(runs, since=since)}"


if __name__ == "__main__":
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    report = build(hours)
    print(report)
    LOG_DIR.mkdir(exist_ok=True)
    # utf-8-SIG, not plain utf-8: the job logs carry em-dashes and accented club
    # names, and Windows tools that default to ANSI (PowerShell 5.1's Get-Content,
    # older Notepad) render those as mojibake without the BOM. A digest nobody
    # can read is no better than the popup it replaced.
    (LOG_DIR / "digest.txt").write_text(report + "\n", encoding="utf-8-sig")
