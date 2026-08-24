"""Unit tests for the football-data.co.uk parsing helpers.

Pure functions, no network/DB. End-to-end correctness is covered separately by
the CSV cross-validation done at ingest time.
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ingestion.team_match import (
    AWAY_MAP,
    HOME_MAP,
    CSV_CORRECTIONS,
    CorrectionError,
    _parse_kickoff,
    _to_int,
    apply_csv_corrections,
)


def test_to_int_handles_floats_blanks_and_nan():
    assert _to_int(3.0) == 3
    assert _to_int("5") == 5
    assert _to_int(None) is None
    assert _to_int(np.nan) is None
    assert _to_int("") is None
    assert _to_int("x") is None


def test_parse_kickoff_is_dayfirst_and_utc():
    d = _parse_kickoff("01/02/2024", None)
    assert (d.year, d.month, d.day) == (2024, 2, 1)  # 1 Feb, not 2 Jan
    assert d.tzinfo == dt.timezone.utc


def test_parse_kickoff_applies_time_when_present():
    d = _parse_kickoff("17/08/2024", "15:00")
    assert (d.hour, d.minute) == (15, 0)


def test_parse_kickoff_returns_none_on_garbage():
    assert _parse_kickoff("not-a-date", None) is None


def _csv_df(rows):
    return pd.DataFrame(rows, columns=["HomeTeam", "AwayTeam", "HC", "AC"])


def test_apply_csv_corrections_overrides_known_bad_cells():
    """The Arsenal-Burnley 2324 CSV carries HC=3; ESPN (and FBref) say 13.
    The correction rewrites the raw cell so BOTH perspectives ingest fixed."""
    df = _csv_df(
        [
            ("Arsenal", "Burnley", 3, 3),
            ("Everton", "Man City", 8, 4),
        ]
    )
    out = apply_csv_corrections(df, "E0", "2324")
    assert out.loc[0, "HC"] == 13 and out.loc[0, "AC"] == 3
    # Everton-Man City is home/away swapped in fd.co.uk -> both cells corrected
    assert out.loc[1, "HC"] == 4 and out.loc[1, "AC"] == 8


def test_apply_csv_corrections_untouched_league_season_passes_through():
    # E2/E3 carry no corrections: the sweep that found them compares against
    # FBref player rows, which League One/Two do not have (ADR 0015).
    df = _csv_df([("Arsenal", "Burnley", 3, 3)])
    out = apply_csv_corrections(df, "E2", "2021")  # no corrections registered
    assert out.loc[0, "HC"] == 3


def test_apply_csv_corrections_fails_loud_when_fixture_missing():
    """A correction that no longer matches its CSV row (renamed team, dropped
    row) must raise, not silently stop applying."""
    df = _csv_df([("Everton", "Man City", 8, 4)])  # Arsenal row absent
    with pytest.raises(CorrectionError):
        apply_csv_corrections(df, "E0", "2324")


def test_csv_corrections_registry_shape():
    """Every registered correction targets a column this ingest actually reads.

    The valid set is derived from HOME_MAP/AWAY_MAP rather than restated, so a
    correction naming a column we never ingest fails here instead of silently
    doing nothing. Corrections were corners-only until the 2026-08-24 sweep
    extended them across the whole event block.
    """
    valid = set(HOME_MAP.values()) | set(AWAY_MAP.values())
    for (key, season, home, away), fixes in CSV_CORRECTIONS.items():
        assert key in ("E0", "E1", "E2", "E3") and len(season) == 4
        assert home and away and fixes
        for col, val in fixes.items():
            assert col in valid and isinstance(val, int)
