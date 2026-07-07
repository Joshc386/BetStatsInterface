"""Tests for international-competition ingestion (ADR 0011).

Pure logic — the August-boundary season tag, the whole-competition (no covered
filter) game selection, and the competition seed — tested with synthetic
schedule DataFrames and the real DB in non-committed sessions. No network; the
live FBref fetch is verified separately under the watchdog.
"""

import datetime as dt

import pandas as pd
from sqlalchemy import select

from app.db import SessionLocal
from app.models.reference import Competition
from ingestion.internationals import (
    LEAGUE_IDS,
    season_for_date,
    select_all_games,
)
from ingestion.seed_competitions import seed_competitions
from ingestion.upcoming import season_for


def _dt(y, m, d):
    return dt.datetime(y, m, d, tzinfo=dt.timezone.utc)


def test_season_for_date_uses_august_boundary():
    """Aug–Dec belong to the season that starts that year; Jan–Jul to the season
    that ends that year — so a June/July summer tournament stays in the season
    just ended, not split at the June/July club-preseason seam."""
    cases = {
        _dt(2022, 11, 20): "2223",  # World Cup 2022 (Qatar, November)
        _dt(2024, 6, 14): "2324",   # Euro 2024 group stage (June)
        _dt(2024, 7, 14): "2324",   # Euro 2024 final (July) — SAME season as group
        _dt(2021, 10, 10): "2122",  # Nations League 2020-21 finals (late, Oct '21)
        _dt(2024, 1, 13): "2324",   # AFCON 2023 (played January 2024)
        _dt(2025, 12, 21): "2526",  # AFCON 2025 (Dec 2025 -> Jan 2026)
        _dt(2026, 1, 18): "2526",
        _dt(2022, 8, 1): "2223",    # August 1 flips to the new season
    }
    for when, expected in cases.items():
        assert season_for_date(when) == expected, when


def test_season_for_date_differs_from_club_july_boundary():
    """The internationals helper deliberately differs from upcoming.season_for
    (July boundary) exactly at July — that is the point of the August boundary."""
    july = _dt(2024, 7, 14)
    assert season_for_date(july) == "2324"
    assert season_for(july) == "2425"  # club-preseason convention splits here


def test_select_all_games_takes_every_played_match_no_covered_filter():
    """Unlike the cup path there is NO covered-tie filter: every row with a
    game_id is kept regardless of the nations involved; unplayed rows (no
    game_id) are dropped; the stored season is derived per-row from the date."""
    df = pd.DataFrame(
        [
            {"home_team": "Argentina", "away_team": "Canada",
             "game_id": "aaaa1111", "date": "2024-06-20", "round": "Group stage"},
            {"home_team": "Tajikistan", "away_team": "Lebanon",
             "game_id": "bbbb2222", "date": "2024-01-17", "round": "Group stage"},
            # not yet played -> dropped
            {"home_team": "Brazil", "away_team": "Uruguay",
             "game_id": None, "date": "2024-07-06", "round": "Quarter-finals"},
        ]
    )
    games = select_all_games(df)
    assert {g["game_id"] for g in games} == {"aaaa1111", "bbbb2222"}
    by_id = {g["game_id"]: g for g in games}
    # season is date-derived, not a single competition-wide value
    assert by_id["aaaa1111"]["season"] == "2324"
    assert by_id["bbbb2222"]["season"] == "2324"
    assert by_id["aaaa1111"]["stage"] == "Group stage"
    # a nation not in our universe is still kept (no filter) — it will auto-create
    assert by_id["bbbb2222"]["home_name"] == "Tajikistan"


def test_select_all_games_blank_round_becomes_empty_stage():
    df = pd.DataFrame(
        [{"home_team": "Spain", "away_team": "France",
          "game_id": "cccc3333", "date": "2024-07-09", "round": None}]
    )
    (game,) = select_all_games(df)
    assert game["stage"] == ""


def test_seeded_international_competitions_present():
    """seed_competitions is idempotent and seeds the 7 finals+NL competitions as
    type 'international' with country NULL; every LEAGUE_IDS name is one of them."""
    seed_competitions()
    with SessionLocal() as session:
        rows = {
            c.name: c
            for c in session.scalars(
                select(Competition).where(Competition.type == "international")
            )
        }
    for name in LEAGUE_IDS:
        assert name in rows, name
        assert rows[name].country is None
        assert rows[name].type == "international"
