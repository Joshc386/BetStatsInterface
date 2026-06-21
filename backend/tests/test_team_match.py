"""Unit tests for the football-data.co.uk parsing helpers.

Pure functions, no network/DB. End-to-end correctness is covered separately by
the CSV cross-validation done at ingest time.
"""

import datetime as dt

import numpy as np

from ingestion.team_match import _parse_kickoff, _to_int


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
