"""Watchdog supervisor for the FBref player backfill.

`backfill_season` runs ONE persistent headful session with no per-fetch timeout,
so a single Cloudflare re-challenge can hang it indefinitely (observed: a 40-min
silent stall mid-run). This supervises the backfill as a subprocess and restarts
it whenever its log goes silent for STALL seconds — the backfill is resumable
(commits per match, skips fixtures already ingested), so a fresh session just
re-solves Cloudflare and continues. Stops when no pending matches remain.

Only the detached `uc_driver.exe` is swept on restart — NEVER `chrome.exe` by
image name (that would also kill the user's own browser). The automation Chrome
is a descendant of the killed process tree / dies with its driver.

Run (VPN OFF):  python -m ingestion.run_backfill 2526
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models.facts import Fixture, PlayerMatch
from app.models.reference import Competition

STALL = 240  # seconds of log silence => assume the session hung (normal match ~15s)
POLL = 15
MAX_RESTARTS = 25
_PY = sys.executable
_LOG = Path(__file__).resolve().parent.parent / "backfill.log"

# Keep the machine awake for the duration. A multi-hour run is useless if the PC
# idle-sleeps — that suspends this process too, so the watchdog can't recover
# (observed: a run froze mid-fetch and sat dead for 10h). SetThreadExecutionState
# with ES_CONTINUOUS holds until we clear it / the process exits; it does NOT
# change any power setting, so nothing to restore.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def _set_sleep_blocked(blocked: bool) -> None:
    flags = _ES_CONTINUOUS | (_ES_SYSTEM_REQUIRED if blocked else 0)
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:  # non-Windows / unavailable — best effort, never fatal
        pass


def _pending(season: str, competition_name: str = "Premier League") -> int:
    """Finished fixtures for this competition-season with no player_match rows.

    Deliberately link-INDEPENDENT (does not require fbref_match_id): the FBref
    linking happens inside backfill_season, so a fresh season has 0 linked
    fixtures up front. Counting by "finished and missing player data" lets the
    watchdog trigger the first run; backfill_season links + ingests from there.
    """
    with SessionLocal() as session:
        comp = session.scalar(
            select(Competition).where(Competition.name == competition_name)
        )
        rows = session.execute(
            select(Fixture.id).where(
                Fixture.competition_id == comp.id,
                Fixture.season == season,
                Fixture.status == "finished",
            )
        ).all()
        return sum(
            1
            for (fixture_id,) in rows
            if session.scalar(
                select(PlayerMatch.id)
                .where(PlayerMatch.fixture_id == fixture_id)
                .limit(1)
            )
            is None
        )


def _kill(proc: subprocess.Popen) -> None:
    # Kill the backfill's whole tree, then sweep the detached uc_driver (it spawns
    # outside the tree). Args passed as a list — no shell, so no flag mangling.
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True
    )
    subprocess.run(["taskkill", "/F", "/IM", "uc_driver.exe"], capture_output=True)


def run(season: str = "2526", competition_name: str = "Premier League") -> int:
    _set_sleep_blocked(True)
    print("[watchdog] system idle-sleep blocked for the run", flush=True)
    try:
        return _run(season, competition_name)
    finally:
        _set_sleep_blocked(False)


def _run(season: str, competition_name: str) -> int:
    pending = _pending(season, competition_name)
    print(f"[watchdog] {competition_name} {season}: {pending} fixtures need "
          f"player data at start", flush=True)

    for attempt in range(1, MAX_RESTARTS + 1):
        if pending == 0:
            print("[watchdog] nothing pending — done", flush=True)
            return 0

        print(f"[watchdog] start attempt {attempt}/{MAX_RESTARTS} "
              f"({pending} pending) -> {_LOG}", flush=True)
        with open(_LOG, "a", encoding="utf-8") as log:
            log.write(f"\n===== watchdog attempt {attempt}: {competition_name} "
                      f"{season} ({pending} pending) =====\n")
            log.flush()
            proc = subprocess.Popen(
                [_PY, "-u", "-m", "ingestion.players", season, competition_name],
                stdout=log, stderr=subprocess.STDOUT,
            )

        # Supervise: restart if the log file stops growing for STALL seconds.
        stalled = False
        while proc.poll() is None:
            time.sleep(POLL)
            idle = time.time() - _LOG.stat().st_mtime
            if idle > STALL:
                print(f"[watchdog] stalled {idle:.0f}s — killing + restarting",
                      flush=True)
                _kill(proc)
                proc.wait()
                stalled = True
                break
        if not stalled:
            print(f"[watchdog] backfill exited (code {proc.returncode})", flush=True)

        time.sleep(3)
        new_pending = _pending(season, competition_name)
        # A clean exit that made no progress means the remaining fixtures have no
        # fetchable FBref match — don't spin restarts on them.
        if not stalled and new_pending >= pending:
            print(f"[watchdog] clean exit, no progress ({new_pending} still pending) "
                  "— remaining fixtures likely unmatched on FBref; stopping.",
                  flush=True)
            return 1
        pending = new_pending

    print(f"[watchdog] hit MAX_RESTARTS with {pending} still pending — "
          "investigate backfill.log", flush=True)
    return 1


if __name__ == "__main__":
    season = sys.argv[1] if len(sys.argv) > 1 else "2526"
    competition = sys.argv[2] if len(sys.argv) > 2 else "Premier League"
    sys.exit(run(season, competition))
