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

import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models.reference import Competition
from ingestion.players import _pending_fixtures

STALL = 240  # seconds of log silence => assume the session hung (normal match ~15s)
POLL = 15
MAX_RESTARTS = 25
_PY = sys.executable
_LOG = Path(__file__).resolve().parent.parent / "backfill.log"


def _pending(season: str, competition_name: str = "Premier League") -> int:
    with SessionLocal() as session:
        comp = session.scalar(
            select(Competition).where(Competition.name == competition_name)
        )
        return len(_pending_fixtures(session, comp, season))


def _kill(proc: subprocess.Popen) -> None:
    # Kill the backfill's whole tree, then sweep the detached uc_driver (it spawns
    # outside the tree). Args passed as a list — no shell, so no flag mangling.
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True
    )
    subprocess.run(["taskkill", "/F", "/IM", "uc_driver.exe"], capture_output=True)


def run(season: str = "2526") -> int:
    pending = _pending(season)
    print(f"[watchdog] {season}: {pending} matches pending at start", flush=True)

    for attempt in range(1, MAX_RESTARTS + 1):
        if pending == 0:
            print("[watchdog] nothing pending — done", flush=True)
            return 0

        print(f"[watchdog] start attempt {attempt}/{MAX_RESTARTS} "
              f"({pending} pending) -> {_LOG}", flush=True)
        with open(_LOG, "a", encoding="utf-8") as log:
            log.write(f"\n===== watchdog attempt {attempt} ({pending} pending) =====\n")
            log.flush()
            proc = subprocess.Popen(
                [_PY, "-u", "-m", "ingestion.players", season],
                stdout=log, stderr=subprocess.STDOUT,
            )

        # Supervise: restart if the log file stops growing for STALL seconds.
        while proc.poll() is None:
            time.sleep(POLL)
            idle = time.time() - _LOG.stat().st_mtime
            if idle > STALL:
                print(f"[watchdog] stalled {idle:.0f}s — killing + restarting",
                      flush=True)
                _kill(proc)
                proc.wait()
                break
        else:
            print(f"[watchdog] backfill exited (code {proc.returncode})", flush=True)

        time.sleep(3)
        pending = _pending(season)

    print(f"[watchdog] hit MAX_RESTARTS with {pending} still pending — "
          "investigate the last match in backfill.log", flush=True)
    return 1


if __name__ == "__main__":
    season = sys.argv[1] if len(sys.argv) > 1 else "2526"
    sys.exit(run(season))
