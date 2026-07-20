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


def test_select_all_games_skips_matches_before_ingest_floor():
    """A qualifying campaign straddling the six-season boundary is held
    partially: matches before INGEST_FLOOR (2020-08-01) never reach the fetch
    list — the dates rule made mechanical (ADR 0011 update 2026-07-09)."""
    df = pd.DataFrame(
        [
            # Euro-2020-qualifying shape: bulk of the campaign in 2019…
            {"home_team": "England", "away_team": "Kosovo",
             "game_id": "out1111a", "date": "2019-09-10", "round": "Group stage"},
            # …exactly at the boundary (kept: floor is inclusive)
            {"home_team": "Wales", "away_team": "Finland",
             "game_id": "edge2222", "date": "2020-08-01", "round": "Group stage"},
            # play-offs a year later (kept)
            {"home_team": "Serbia", "away_team": "Scotland",
             "game_id": "keep3333", "date": "2020-11-12", "round": "Play-offs"},
        ]
    )
    games = select_all_games(df)
    assert {g["game_id"] for g in games} == {"edge2222", "keep3333"}


def test_select_all_games_drops_dateless_rows():
    """A game_id with no date (a cancelled match FBref still lists — OFC 2022
    Tonga–Cook Islands) is dropped: it cannot be season-tagged, and NaT would
    otherwise crash season_for_date after slipping past the floor comparison."""
    df = pd.DataFrame(
        [
            {"home_team": "Tonga", "away_team": "Cook Islands",
             "game_id": "c714719c", "date": None, "round": "Qualification match"},
            {"home_team": "Solomon Islands", "away_team": "Tahiti",
             "game_id": "keep4444", "date": "2022-03-17", "round": "Group stage"},
        ]
    )
    games = select_all_games(df)
    assert [g["game_id"] for g in games] == ["keep4444"]


def test_league_ids_maps_all_wc_qualifying_to_one_competition():
    """Every WC qualifying selector — all confederations + the inter-confed
    play-offs — stores under the single 'World Cup Qualifiers' competition
    (region is self-evident from the nations); other selectors are 1:1, and no
    two selectors share a soccerdata league key."""
    wcq = {sel: comp for sel, (comp, _lg) in LEAGUE_IDS.items()
           if sel.startswith("WC Qual")}
    assert len(wcq) == 7  # 6 confederations + play-offs
    assert set(wcq.values()) == {"World Cup Qualifiers"}
    for sel, (comp, _lg) in LEAGUE_IDS.items():
        if not sel.startswith("WC Qual"):
            assert comp == sel, sel  # 1:1 everywhere else
    leagues = [lg for (_c, lg) in LEAGUE_IDS.values()]
    assert len(leagues) == len(set(leagues))


def test_seeded_international_competitions_present():
    """seed_competitions is idempotent and seeds every international competition
    LEAGUE_IDS can store into — 7 finals+NL plus the 4 qualifying competitions —
    as type 'international' with country NULL."""
    seed_competitions()
    with SessionLocal() as session:
        rows = {
            c.name: c
            for c in session.scalars(
                select(Competition).where(Competition.type == "international")
            )
        }
    comp_names = {comp for (comp, _lg) in LEAGUE_IDS.values()}
    assert len(comp_names) == 11
    for name in comp_names:
        assert name in rows, name
        assert rows[name].country is None
        assert rows[name].type == "international"
