"""Tests for domestic-cup player ingestion (Phase B, ADR 0008).

Pure logic (covered-tie filter, opponent auto-create, fixture get-or-create,
schedule filtering) is tested with the real DB in rolled-back sessions and with
synthetic schedule DataFrames — no network. The live cup fetch is verified
separately under the watchdog (Phase B.2).
"""

import datetime as dt

import pandas as pd
from sqlalchemy import select, text

from ingestion.cups import (
    _already_ingested,
    covered_team_ids,
    get_or_create_cup_fixture,
    pair_team_ids,
    resolve_or_create_fbref_team,
    select_covered_games,
)
from app.db import SessionLocal
from app.models.facts import Fixture, PlayerMatch, TeamMatch
from app.models.reference import Competition, Player, Team


def test_covered_team_ids_match_pl_and_championship_league_rows():
    """A team is covered for a season iff it has a Premier League / Championship
    team_match row that season — nothing lower, nothing from another season."""
    season = "2425"
    with SessionLocal() as session:
        covered = covered_team_ids(session, season)
        expected = set(
            session.scalars(
                select(TeamMatch.team_id)
                .join(Competition, Competition.id == TeamMatch.competition_id)
                .where(
                    TeamMatch.season == season,
                    Competition.name.in_(["Premier League", "Championship"]),
                )
            )
        )
        assert covered == expected and len(covered) > 0


def test_covered_team_ids_excludes_some_lower_league_teams():
    """The filter genuinely excludes — at least one team with a team_match row
    that season is not covered (a League One/Two side)."""
    season = "2425"
    with SessionLocal() as session:
        covered = covered_team_ids(session, season)
        any_team_match = set(
            session.scalars(
                select(TeamMatch.team_id).where(TeamMatch.season == season)
            )
        )
        assert any_team_match - covered  # non-empty: some teams are filtered out


def test_already_ingested_skips_games_with_player_rows():
    """Resumability: a covered game whose fixture already has player_match rows is
    skipped on restart (no re-fetch); an unknown/empty game id is not."""
    with SessionLocal() as session:
        comp = session.scalar(select(Competition).where(Competition.name == "FA Cup"))
        assert _already_ingested(session, "zznogame") is False
        a = Team(canonical_name="__ZZ Resume Home__")
        b = Team(canonical_name="__ZZ Resume Away__")
        session.add_all([a, b])
        session.flush()
        when = dt.datetime(2025, 1, 11, tzinfo=dt.timezone.utc)
        fx = get_or_create_cup_fixture(session, comp, "2425", a, b, when, "zzresume1")
        # fixture exists but no player rows yet -> not ingested
        assert _already_ingested(session, "zzresume1") is False
        p = Player(canonical_name="__ZZ Resume Player__", fbref_id="zzrp01")
        session.add(p)
        session.flush()
        session.add(
            PlayerMatch(
                fixture_id=fx.id, competition_id=comp.id, competition_type="club_cup",
                season="2425", date=when, player_id=p.id, team_id=a.id,
                opponent_id=b.id, is_home=True, minutes=90,
            )
        )
        session.flush()
        assert _already_ingested(session, "zzresume1") is True
        session.rollback()


def test_one_cup_fixture_per_fbref_match_id():
    """Standing regression guard (ADR 0008): a cup fbref_match_id maps to exactly
    one fixture. The play-offs proved this is where contamination sneaks in (a
    league/cup natural-key collision reusing a row), so it is guarded explicitly.
    Read-only; empty-true until cup data lands, meaningful thereafter.
    """
    with SessionLocal() as session:
        violations = session.execute(
            text(
                """
                SELECT f.fbref_match_id, COUNT(*) AS n
                FROM fixtures f
                JOIN competitions c ON c.id = f.competition_id
                WHERE c.type = 'club_cup' AND f.fbref_match_id IS NOT NULL
                GROUP BY f.fbref_match_id
                HAVING COUNT(*) > 1
                """
            )
        ).all()
        assert not violations, (
            f"{len(violations)} cup match ids mapped to >1 fixture: "
            f"{[tuple(v) for v in violations[:10]]}"
        )


def test_resolve_or_create_returns_existing_by_id():
    with SessionLocal() as session:
        t = Team(canonical_name="__ZZ Cup Existing__", fbref_id="zzcup001")
        session.add(t)
        session.flush()
        got, created = resolve_or_create_fbref_team(
            session, "Anything", fbref_id="zzcup001"
        )
        assert got.id == t.id and created is False
        session.rollback()


def test_resolve_or_create_makes_a_new_team_for_unknown_opponent():
    with SessionLocal() as session:
        got, created = resolve_or_create_fbref_team(
            session, "__ZZ Non-League Wanderers__", fbref_id="zzcup999"
        )
        assert created is True
        assert got.fbref_id == "zzcup999"
        assert got.canonical_name == "__ZZ Non-League Wanderers__"
        session.rollback()


def test_pair_team_ids_matches_schedule_names_to_caption_ids():
    caps = {"Manchester Utd": "19538871", "Cardiff City": "75fae011"}
    # schedule spells "Cardiff" / "Manchester Utd"; FC/case folded for the match
    assert pair_team_ids("Manchester Utd", "Cardiff City FC", caps) == (
        "19538871",
        "75fae011",
    )
    # an unmatchable name yields None for that side
    assert pair_team_ids("Manchester Utd", "Unknown Town", caps) == (
        "19538871",
        None,
    )


def test_select_covered_games_keeps_only_ties_with_a_covered_side():
    """A synthetic cup schedule: keep ties with >=1 Premier League / Championship
    club that season, drop all-lower-league ties and rows with no game_id."""
    df = pd.DataFrame(
        [
            # covered home (Man Utd is PL 24-25) vs unknown opponent -> keep
            {"home_team": "Manchester Utd", "away_team": "__ZZ Tiny FC__",
             "game_id": "aaaa1111", "date": "2024-11-01"},
            # two unknowns -> drop
            {"home_team": "__ZZ Tiny FC__", "away_team": "__ZZ Other Tiny__",
             "game_id": "bbbb2222", "date": "2024-11-01"},
            # covered away -> keep
            {"home_team": "__ZZ Tiny FC__", "away_team": "Wolves",
             "game_id": "cccc3333", "date": "2024-11-02"},
            # covered tie but no game_id (not yet played) -> drop
            {"home_team": "Wolves", "away_team": "Manchester Utd",
             "game_id": None, "date": "2024-11-02"},
        ]
    )
    with SessionLocal() as session:
        games = select_covered_games(session, df, "2425")
        assert {g["game_id"] for g in games} == {"aaaa1111", "cccc3333"}


def test_get_or_create_cup_fixture_is_idempotent():
    with SessionLocal() as session:
        comp = session.scalar(select(Competition).where(Competition.name == "FA Cup"))
        assert comp is not None  # seeded in step 1
        a = Team(canonical_name="__ZZ Cup Home__")
        b = Team(canonical_name="__ZZ Cup Away__")
        session.add_all([a, b])
        session.flush()
        when = dt.datetime(2025, 1, 11, tzinfo=dt.timezone.utc)

        f1 = get_or_create_cup_fixture(session, comp, "2425", a, b, when, "dddd4444")
        f2 = get_or_create_cup_fixture(session, comp, "2425", a, b, when, "dddd4444")
        assert f1.id == f2.id
        assert f1.fbref_match_id == "dddd4444"
        count = session.scalar(
            select(Fixture).where(Fixture.fbref_match_id == "dddd4444")
        )
        assert count is not None
        session.rollback()


def test_fbref_player_df_long_names_resolve_to_existing_canonicals():
    """FA Cup player-df spells lower-league clubs in full ('Accrington Stanley')
    while football-data canonical rows use the short name ('Accrington'). Both the
    player-df spelling and the schedule/caption spelling must resolve to the
    existing row, not fail-loud — the two-spelling seam that silently dropped 7
    covered FA Cup 2024-25 ties (ADR 0008)."""
    from ingestion.players import match_existing_team

    cases = {
        "Peterborough United": "Peterboro",  # player-df spelling
        "Peterborough": "Peterboro",  # schedule / caption spelling
        "Accrington Stanley": "Accrington",
        "Doncaster Rovers": "Doncaster",
        "Wycombe Wanderers": "Wycombe",
    }
    with SessionLocal() as session:
        for fbref_name, canonical in cases.items():
            team = match_existing_team(session, fbref_name)
            assert team is not None, f"{fbref_name!r} did not resolve (needs alias)"
            assert team.canonical_name == canonical, (
                f"{fbref_name!r} -> {team.canonical_name!r}, expected {canonical!r}"
            )


def test_two_spelling_new_opponent_resolves_to_same_row():
    """A genuinely-new non-league opponent is spelled one way in the schedule
    caption ('Dag & Red', which keys/auto-creates the row) and another in the
    per-match player df ('Dagenham & Redbridge'). Both must resolve to the SAME
    team via the alias — never fail-loud, else the tie AND the new team roll back
    (the second half of the FA Cup 2024-25 drop). State-independent: holds whether
    or not the row is already ingested. Rolled back."""
    from ingestion.players import resolve_fbref_team

    with SessionLocal() as session:
        by_caption, _ = resolve_or_create_fbref_team(
            session, "Dag & Red", fbref_id="zzdag_seam"
        )
        # mirror ingest_match: name-only resolution of the player-df spelling
        by_player_df = resolve_fbref_team(session, "Dagenham & Redbridge")
        assert by_player_df.id == by_caption.id
        session.rollback()


def test_resolve_or_create_links_handle_onto_existing_row_no_duplicate():
    """An unknown-by-id opponent whose name matches an existing row attaches its
    fbref_id to that row (the seam link) rather than creating a duplicate."""
    with SessionLocal() as session:
        t = Team(canonical_name="__ZZ Seam Town__", fdcouk_name="__ZZ Seam Town__")
        session.add(t)
        session.flush()
        got, created = resolve_or_create_fbref_team(
            session, "__ZZ Seam Town__ FC", fbref_id="zzseam01"
        )
        assert created is False
        assert got.id == t.id
        assert got.fbref_id == "zzseam01"
        session.rollback()
