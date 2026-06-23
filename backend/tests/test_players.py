"""Tests for FBref player-match ingestion (Phase 4).

Pure-function tests parse the *cached* FBref match HTML and schedule — no network.
The DB idempotency test runs in a rolled-back session and leaves nothing behind.
Requires the soccerdata FBref cache (match_cc5b4244.html etc.) and DATABASE_URL.
"""

from pathlib import Path

import pytest

from sqlalchemy import func, select

from ingestion.players import (
    UnknownTeamError,
    canonical_team_name,
    ingest_match,
    link_fixtures,
    parse_player_ids,
    resolve_fbref_team,
    summary_to_player_stats,
)
from app.db import SessionLocal
from app.models.facts import Fixture, PlayerMatch
from app.models.reference import Competition, Player

CACHE = Path.home() / "soccerdata" / "data" / "FBref"
# PL 2024-25, Manchester Utd 1-0 Fulham (matchweek 1).
MATCH_CC5B4244 = CACHE / "match_cc5b4244.html"
# All four cached PL 2024-25 matchweek-1 pages.
CACHED_MATCH_IDS = ["cc5b4244", "c0e3342a", "71618ace", "a1d0d529"]


def _html(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"cached FBref page not present: {path}")
    return path.read_text(encoding="utf-8")


def _summary_df(match_id: str):
    """Load one match's summary stats from the soccerdata cache (no network)."""
    import logging

    logging.disable(logging.CRITICAL)
    import soccerdata as sd

    fb = sd.FBref(leagues="ENG-Premier League", seasons="2425")
    return fb.read_player_match_stats(
        stat_type="summary", match_id=match_id, force_cache=True
    )


def _schedule_df(season: str):
    """Load the PL schedule for a season from the soccerdata cache (no network)."""
    import logging

    logging.disable(logging.CRITICAL)
    import soccerdata as sd

    fb = sd.FBref(leagues="ENG-Premier League", seasons=season)
    return fb.read_schedule(force_cache=True)


def test_parse_player_ids_is_scoped_to_lineups():
    """Returns only the two lineups' players, not the page's comparison widgets.

    A blanket href grep on this page returns ~100 ids (Paul Scholes, Abby
    Wambach, etc.); the real two squads are ~22-36 players.
    """
    ids = parse_player_ids(_html(MATCH_CC5B4244))
    assert 20 <= len(ids) <= 40
    # A known Fulham starter, with the id embedded in its /en/players/ href.
    assert ids["Antonee Robinson"] == "289601e6"
    # The comparison-widget noise must be excluded.
    assert "Paul Scholes" not in ids


@pytest.mark.parametrize("match_id", CACHED_MATCH_IDS)
def test_every_df_player_resolves_to_an_id(match_id):
    """The HTML id map must cover every player in the summary DataFrame.

    The join (df player name -> fbref_id) has to be total — a missing name
    would silently drop that player's player_match row.
    """
    df = _summary_df(match_id)
    ids = parse_player_ids(_html(CACHE / f"match_{match_id}.html"))
    df_names = set(df.reset_index()["player"])
    missing = df_names - set(ids)
    assert not missing, f"{match_id}: df players with no parsed id: {sorted(missing)}"


def test_canonical_team_name_maps_known_fbref_diffs():
    assert canonical_team_name("Manchester Utd") == "Man United"
    assert canonical_team_name("Manchester City") == "Man City"
    assert canonical_team_name("Ipswich Town") == "Ipswich"
    assert canonical_team_name("Leeds United") == "Leeds"
    assert canonical_team_name("Leicester City") == "Leicester"
    assert canonical_team_name("Nottingham") == "Nott'm Forest"


def test_canonical_team_name_maps_player_df_full_names():
    """The player_match df uses full club names, distinct from the schedule's
    short names. These four are observed in the cached match pages."""
    assert canonical_team_name("Manchester United") == "Man United"
    assert canonical_team_name("Brighton & Hove Albion") == "Brighton"
    assert canonical_team_name("Wolverhampton Wanderers") == "Wolves"
    assert canonical_team_name("Ipswich Town") == "Ipswich"


def test_canonical_team_name_passes_identity_names_through():
    # Names that already match the canonical (football-data) spelling — just cleaned.
    assert canonical_team_name("Arsenal") == "Arsenal"
    assert canonical_team_name("  Fulham ") == "Fulham"
    assert canonical_team_name("Wolves") == "Wolves"


def test_resolve_fbref_team_maps_alias_to_canonical_row():
    session = SessionLocal()
    try:
        team = resolve_fbref_team(session, "Manchester Utd")
        assert team.canonical_name == "Man United"
    finally:
        session.rollback()
        session.close()


def test_resolve_fbref_team_raises_on_unknown_team():
    session = SessionLocal()
    try:
        with pytest.raises(UnknownTeamError):
            resolve_fbref_team(session, "__No Such Club FC__")
    finally:
        session.rollback()
        session.close()


def test_summary_to_player_stats_maps_metrics():
    """Spot-check the column mapping against known cc5b4244 values, incl. the
    FBref-specific TklW->tackles and Fld/Fls fouls split."""
    rows = summary_to_player_stats(_summary_df("cc5b4244"))
    by_name = {r["player"]: r for r in rows}

    bruno = by_name["Bruno Fernandes"]
    assert bruno["team"] == "Manchester United"  # df uses the full club name
    assert bruno["minutes"] == 90
    assert bruno["shots"] == 6
    assert bruno["sot"] == 3
    assert bruno["tackles"] == 1  # FBref TklW
    assert bruno["goals"] == 0
    assert bruno["fouls_committed"] == 0

    mount = by_name["Mason Mount"]
    assert mount["minutes"] == 60
    assert mount["yellows"] == 1
    assert mount["fouls_committed"] == 1  # Fls
    assert mount["fouls_drawn"] == 1  # Fld

    zirkzee = by_name["Joshua Zirkzee"]
    assert zirkzee["goals"] == 1
    garnacho = by_name["Alejandro Garnacho"]
    assert garnacho["assists"] == 1


def _cc5b4244_fixture(session):
    """The existing PL 2024-25 Man Utd v Fulham fixture (created in Phase 3)."""
    comp = session.scalar(
        select(Competition).where(Competition.name == "Premier League")
    )
    home = resolve_fbref_team(session, "Manchester United")
    away = resolve_fbref_team(session, "Fulham")
    return session.scalar(
        select(Fixture).where(
            Fixture.competition_id == comp.id,
            Fixture.season == "2425",
            Fixture.home_team_id == home.id,
            Fixture.away_team_id == away.id,
        )
    ), home, away


def test_ingest_match_is_idempotent_and_joins_correctly():
    session = SessionLocal()
    try:
        fixture, home, _away = _cc5b4244_fixture(session)
        assert fixture is not None, "Phase 3 should have created this PL fixture"

        df = _summary_df("cc5b4244")
        ids = parse_player_ids(_html(MATCH_CC5B4244))

        def count_pm():
            return session.scalar(
                select(func.count())
                .select_from(PlayerMatch)
                .where(PlayerMatch.fixture_id == fixture.id)
            )

        n1 = ingest_match(session, fixture, "club_league", df, ids)
        session.flush()
        c1 = count_pm()
        n2 = ingest_match(session, fixture, "club_league", df, ids)
        session.flush()
        c2 = count_pm()

        # Re-running upserts in place — no duplicate player_match rows.
        assert n1 == n2 >= 20
        assert c1 == c2 == n1
        # One Player per fbref id, even across the two runs.
        assert (
            session.scalar(
                select(func.count())
                .select_from(Player)
                .where(Player.fbref_id == "507c7bdf")  # Bruno Fernandes
            )
            == 1
        )

        # The HTML-id -> player -> player_match join lands on the right team/side.
        bruno_pid = session.scalar(
            select(Player.id).where(Player.fbref_id == "507c7bdf")
        )
        pm = session.scalar(
            select(PlayerMatch).where(
                PlayerMatch.fixture_id == fixture.id,
                PlayerMatch.player_id == bruno_pid,
            )
        )
        assert pm.is_home is True
        assert pm.team_id == home.id
        assert pm.minutes == 90
        assert pm.tackles == 1
        assert pm.carded is False  # generated column: no cards
    finally:
        session.rollback()
        session.close()


def test_link_fixtures_sets_fbref_match_id_idempotently():
    session = SessionLocal()
    try:
        comp = session.scalar(
            select(Competition).where(Competition.name == "Premier League")
        )
        sched = _schedule_df("2425")

        r1 = link_fixtures(session, comp, "2425", sched)
        session.flush()

        fixture, _home, _away = _cc5b4244_fixture(session)
        assert fixture.fbref_match_id == "cc5b4244"
        # A full PL season is 380 fixtures; almost all should link.
        assert r1["linked"] >= 370

        r2 = link_fixtures(session, comp, "2425", sched)
        assert r2["linked"] == r1["linked"]  # idempotent
    finally:
        session.rollback()
        session.close()
