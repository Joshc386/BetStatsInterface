"""Hardened FBref fetch runner.

Every FBref call runs in an isolated subprocess (ingestion.fbref_worker) with a
hard timeout. On hang, the entire process tree (python + uc_driver + Chrome) is
force-killed via `taskkill /T` — which targets only this worker's descendants,
never the user's own Chrome. Failed fetches retry, then return None (caller skips).

soccerdata caches every successful page, so retries and re-runs are cheap.
NOTE: FBref ingestion requires the VPN to be OFF (Cloudflare blocks the VPN IP).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

_WORKER = Path(__file__).resolve().parent / "fbref_worker.py"
_PYTHON = sys.executable


def _kill_tree(pid: int) -> None:
    # Kill the worker tree, then sweep any detached uc_driver (undetected-chromedriver
    # spawns it outside the worker's process tree, so /T alone misses it).
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, text=True)
    subprocess.run(["taskkill", "/F", "/IM", "uc_driver.exe"], capture_output=True, text=True)


def fbref_fetch(
    spec: dict, *, timeout: float = 180.0, retries: int = 2
) -> pd.DataFrame | None:
    """Fetch one FBref resource with isolation + timeout + retry.

    Returns the DataFrame, or None if every attempt hung or errored.
    """
    for attempt in range(1, retries + 2):
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tmp:
            out_path = tmp.name
        proc = subprocess.Popen(
            [_PYTHON, str(_WORKER), json.dumps(spec), out_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            proc.communicate(timeout=timeout)
            if proc.returncode == 0:
                df = pd.read_pickle(out_path)
                Path(out_path).unlink(missing_ok=True)
                return df
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            proc.wait()
        finally:
            Path(out_path).unlink(missing_ok=True)
    return None


def fetch_schedule(
    leagues: list[str], season: str, headless: bool = True, **kw
) -> pd.DataFrame | None:
    return fbref_fetch(
        {"leagues": leagues, "seasons": [season], "kind": "schedule", "headless": headless},
        **kw,
    )


def fetch_match_players(
    leagues: list[str], season: str, match_id, **kw
) -> pd.DataFrame | None:
    return fbref_fetch(
        {
            "leagues": leagues,
            "seasons": [season],
            "kind": "player_match",
            "match_id": match_id,
        },
        **kw,
    )
