"""Every entry-point module must import on its own, in a fresh interpreter.

Regression test for a real escape (2026-08-23): ingestion/coverage.py imported
LEAGUE_PLAYER_COMPETITIONS from ingestion/matchday.py while matchday imported
coverage — a cycle that resolves or fails purely on import ORDER. The whole test
suite passed, because `from ingestion import coverage, matchday` loads coverage
first and that direction happens to work. `python -m ingestion.matchday` — the
actual scheduled command — died with ImportError.

Importing inside this process would not catch it: pytest has already imported
these modules, so sys.modules hides the cycle. Each check must be a subprocess.
"""

import subprocess
import sys

import pytest

ENTRY_POINTS = [
    "ingestion.matchday",
    "ingestion.nightly",
    "ingestion.upcoming",
    "ingestion.coverage",
    "ingestion.squads",
    "ingestion.players",
    "ingestion.run_backfill",
    "ingestion.cups",
    "ingestion.team_match",
    "app.main",
]


@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_module_imports_standalone(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"`import {module}` fails on its own:\n{result.stderr}"
    )
