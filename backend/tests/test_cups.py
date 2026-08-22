"""Tests for domestic-cup player ingestion (Phase B, ADR 0008).

Pure logic (covered-tie filter, opponent auto-create, fixture get-or-create,
schedule filtering) is tested with the real DB in rolled-back sessions and with
synthetic schedule DataFrames — no network. The live cup fetch is verified
separately under the watchdog (Phase B.2).
"""

import datetime as dt

import pandas as pd
from sqlalchemy import select, text

import pytest

from ingestion.cups import (
    _already_ingested,
    _guard_token,
    _team_stat_totals,
    covered_divergence,
    covered_fbref_ids,
    exit_code,
    covered_team_ids,
    tie_is_covered,
    get_or_create_cup_fixture,
    pair_team_ids,
    resolve_or_create_fbref_team,
    select_covered_games,
    split_country_code,
)
from ingestion.players import UnknownTeamError
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
                WHERE c.type IN ('club_cup', 'club_european')
                  AND f.fbref_match_id IS NOT NULL
                GROUP BY f.fbref_match_id
                HAVING COUNT(*) > 1
                """
            )
        ).all()
        assert not violations, (
            f"{len(violations)} cup match ids mapped to >1 fixture: "
            f"{[tuple(v) for v in violations[:10]]}"
        )


def test_parse_scoreline_reads_home_then_away_ignoring_shootout():
    """gf/ga = the two scorebox `.score` blocks in document order; a penalty
    shootout (`.score_pen`) is deliberately not counted as goals."""
    from ingestion.players import parse_scoreline

    html = (
        '<div class="scorebox">'
        '<div><div class="scores"><div class="score">3</div>'
        '<div class="score_pen">4</div></div></div>'
        '<div><div class="scores"><div class="score">1</div></div></div>'
        "</div>"
    )
    assert parse_scoreline(html) == (3, 1)


def test_parse_scoreline_handles_double_digit_score():
    """A 10+ score gets class 'score double'; the token match must still read it
    (regression: FA Cup 2025-26 Man City 10-1 Exeter was dropped by an exact
    class='score' match). The 'scores' container is not double-counted."""
    from ingestion.players import parse_scoreline

    html = (
        '<div class="scorebox">'
        '<div><div class="scores"><div class="score double">10</div></div></div>'
        '<div><div class="scores"><div class="score">1</div></div></div>'
        "</div>"
    )
    assert parse_scoreline(html) == (10, 1)


def test_parse_scoreline_raises_without_scorebox():
    from ingestion.players import ScorelineError, parse_scoreline

    with pytest.raises(ScorelineError):
        parse_scoreline("<div>no scorebox here</div>")


# Modelled on the real team_stats_extra markup: groups of home/label/away cell
# triplets, each group headed by three .th cells (team names + blank).
_EXTRA_PANEL = (
    '<div id="team_stats_extra">'
    "<div>"
    '<div class="th">Manchester Utd</div><div class="th"> </div><div class="th">Fulham</div>'
    "<div>12</div><div>Fouls</div><div>10</div>"
    "<div>7</div><div>Corners</div><div>8</div>"
    "<div>18</div><div>Crosses</div><div>21</div>"
    "</div>"
    "<div>"
    '<div class="th">Manchester Utd</div><div class="th"> </div><div class="th">Fulham</div>'
    "<div>17</div><div>Interceptions</div><div>10</div>"
    "</div>"
    "</div>"
)


def test_parse_corners_reads_home_then_away():
    """Corners = the home/Corners/away triplet in the team_stats_extra panel;
    the .th header row and the other stat rows must not shift the triplets."""
    from ingestion.players import parse_corners

    assert parse_corners(_EXTRA_PANEL) == (7, 8)


def test_parse_corners_none_without_panel():
    """Some cup pages (FA Cup third-round ties) genuinely lack the panel —
    corners is None (honest NULL), never an error that would skip the fixture's
    team rows."""
    from ingestion.players import parse_corners

    assert parse_corners('<div class="scorebox">no extra panel</div>') is None


def test_parse_corners_none_when_panel_has_no_corners_row():
    from ingestion.players import parse_corners

    html = (
        '<div id="team_stats_extra"><div>'
        '<div class="th">A</div><div class="th"> </div><div class="th">B</div>'
        "<div>12</div><div>Fouls</div><div>10</div>"
        "</div></div>"
    )
    assert parse_corners(html) is None


@pytest.mark.parametrize(
    "match_id,expected",
    [
        ("cc5b4244", (7, 8)),  # PL 2425: Manchester Utd 1-0 Fulham
        ("bee4cf99", (2, 6)),  # EFL Cup 2324: Cheltenham vs Birmingham City
    ],
)
def test_parse_corners_on_cached_pages(match_id, expected):
    """Real cached pages, league and cup, parse to their known corner counts."""
    from pathlib import Path

    from ingestion.players import parse_corners

    page = Path.home() / "soccerdata" / "data" / "FBref" / f"match_{match_id}.html"
    if not page.exists():
        pytest.skip(f"cached FBref page not present: {page}")
    assert parse_corners(page.read_text(encoding="utf-8")) == expected


def test_team_stat_totals_sums_rows_and_leaves_sparse_metrics_null():
    """Team totals = SUM of the fixture's player rows; a side whose players all
    carry NULL shots (source-sparse match) yields NULL, not 0 — uncovered, not
    zeroed. Rolled back."""
    import datetime as dt

    with SessionLocal() as session:
        comp = session.scalar(select(Competition).where(Competition.name == "FA Cup"))
        a = Team(canonical_name="__ZZ TM Home__")
        b = Team(canonical_name="__ZZ TM Away__")
        session.add_all([a, b])
        session.flush()
        when = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        fx = get_or_create_cup_fixture(session, comp, "2425", a, b, when, "zztmtot1")
        p1 = Player(canonical_name="__ZZ TM P1__", fbref_id="zztm_p1")
        p2 = Player(canonical_name="__ZZ TM P2__", fbref_id="zztm_p2")
        session.add_all([p1, p2])
        session.flush()
        common = dict(
            fixture_id=fx.id, competition_id=comp.id, competition_type="club_cup",
            season="2425", date=when, minutes=90,
        )
        session.add_all([
            PlayerMatch(**common, player_id=p1.id, team_id=a.id, opponent_id=b.id,
                        is_home=True, shots=3, sot=1, fouls_committed=2, yellows=1, reds=0),
            # away side: shots/sot/fouls NULL (sparse), cards present
            PlayerMatch(**common, player_id=p2.id, team_id=b.id, opponent_id=a.id,
                        is_home=False, shots=None, sot=None, fouls_committed=None,
                        yellows=0, reds=0),
        ])
        session.flush()

        totals = _team_stat_totals(session, fx.id)
        assert totals[a.id] == {"shots": 3, "sot": 1, "fouls": 2, "yellows": 1, "reds": 0}
        assert totals[b.id]["shots"] is None and totals[b.id]["sot"] is None
        assert totals[b.id]["fouls"] is None and totals[b.id]["yellows"] == 0
        session.rollback()


def test_cup_fixture_team_match_rows_are_zero_or_two_never_partial():
    """Standing guard: a cup fixture carries either 0 team_match rows (team data
    not built for its competition) or exactly 2 (both sides) — never 1 or 3+, which
    would mean a half-ingested or contaminated fixture. Read-only."""
    with SessionLocal() as session:
        violations = session.execute(
            text(
                """
                SELECT f.id, f.fbref_match_id, COUNT(tm.id) AS n
                FROM fixtures f
                JOIN competitions c ON c.id = f.competition_id
                    AND c.type IN ('club_cup', 'club_european')
                LEFT JOIN team_match tm ON tm.fixture_id = f.id
                GROUP BY f.id, f.fbref_match_id
                HAVING COUNT(tm.id) NOT IN (0, 2)
                """
            )
        ).all()
        assert not violations, (
            f"{len(violations)} cup fixtures with a partial team_match set: "
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


def test_resolve_or_create_refuses_likely_duplicate_of_existing_club():
    """Regression: 14 in-universe clubs were silently duplicated because their
    FBref full name ("Bradford City") didn't alias to the fd.co.uk canonical
    ("Bradford"). A shared first word must now fail loud, not create a row."""
    with SessionLocal() as session:
        session.add(Team(canonical_name="__ZZGuardtown", fdcouk_name="__ZZGuardtown"))
        session.flush()
        with pytest.raises(UnknownTeamError, match="refusing to auto-create"):
            resolve_or_create_fbref_team(
                session, "__ZZGuardtown Rovers", fbref_id="zzcup998"
            )
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
        "Bolton Wanderers": "Bolton",
        "Cambridge United": "Cambridge",
        "Cheltenham Town": "Cheltenham",
        "Shrewsbury Town": "Shrewsbury",
        "MK Dons": "Milton Keynes Dons",  # short caption -> long canonical
        "Colchester United": "Colchester",
        "Northampton Town": "Northampton",
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
    (the second half of the FA Cup 2024-25 drop). Rolled back.

    The caption id used here is whatever the row actually holds. It used to be a
    synthetic one, which since the identity guard simulates an impossible state:
    a club stored with id X can only ever be captioned X by FBref, so a
    conflicting id now means a DIFFERENT club and is refused by design."""
    from ingestion.players import resolve_fbref_team

    with SessionLocal() as session:
        stored = session.scalar(select(Team).where(Team.canonical_name == "Dag & Red"))
        by_caption, _ = resolve_or_create_fbref_team(
            session, "Dag & Red", fbref_id=stored.fbref_id or "zzdag_seam"
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


# --- European extension (ADR 0011) ------------------------------------------


def test_stage_disambiguates_european_rematches():
    """The Benfica-Barcelona case: the same pairing at the same venue recurs in
    one European season across stages (league phase + knockout leg). Different
    stage -> distinct fixtures; same stage -> idempotent get-or-create. Rolled
    back."""
    with SessionLocal() as session:
        comp = session.scalar(
            select(Competition).where(Competition.name == "Champions League")
        )
        assert comp is not None and comp.type == "club_european"
        a = Team(canonical_name="__ZZ Euro Hosts__")
        b = Team(canonical_name="__YY Euro Visitors__")
        session.add_all([a, b])
        session.flush()
        d1 = dt.datetime(2025, 10, 1, tzinfo=dt.timezone.utc)
        d2 = dt.datetime(2026, 2, 18, tzinfo=dt.timezone.utc)

        league_phase = get_or_create_cup_fixture(
            session, comp, "2526", a, b, d1, "zzeur001", stage="League phase"
        )
        knockout = get_or_create_cup_fixture(
            session, comp, "2526", a, b, d2, "zzeur002", stage="Round of 16"
        )
        assert league_phase.id != knockout.id  # two real fixtures, no rejection

        again = get_or_create_cup_fixture(
            session, comp, "2526", a, b, d1, "zzeur001", stage="League phase"
        )
        assert again.id == league_phase.id  # same stage -> idempotent
        session.rollback()


def test_guard_token_skips_generic_prefixes():
    """At European volume, first-word matching would deadlock unrelated clubs
    ('FC Porto' vs 'FC Copenhagen'); the guard token is the first NON-generic
    token, which still catches the alt-spelling signature it exists for."""
    assert _guard_token("FC Porto") == "porto"
    assert _guard_token("FC Copenhagen") == "copenhagen"
    assert _guard_token("Real Madrid") == "madrid"
    assert _guard_token("Real Sociedad") == "sociedad"
    assert _guard_token("AFC Wimbledon") == "wimbledon"
    assert _guard_token("1. FC Köln") == "köln"
    # acronym clubs: AEK Athens and AEK Larnaca are distinct (first live trip)
    assert _guard_token("AEK Athens") == "athens"
    assert _guard_token("AEK Larnaca") == "larnaca"
    # the original duplicate signature still collides
    assert _guard_token("Bradford City") == _guard_token("Bradford")
    # an all-generic name degrades to its first token, never crashes
    assert _guard_token("Real") == "real"


def test_generic_prefix_clubs_do_not_deadlock_creation():
    """Two genuinely-distinct clubs sharing only a generic prefix are both
    auto-created; no allowlist entry needed. Rolled back."""
    with SessionLocal() as session:
        first, created1 = resolve_or_create_fbref_team(
            session, "FC __ZZportu__", fbref_id="zzeurt01", country="Portugal"
        )
        second, created2 = resolve_or_create_fbref_team(
            session, "FC __YYcopen__", fbref_id="zzeurt02", country="Denmark"
        )
        assert created1 and created2
        assert first.country == "Portugal" and second.country == "Denmark"
        session.rollback()


def test_split_country_code_handles_lead_trail_and_absence():
    """FBref European schedules carry a country code adjacent to team names
    (leading or trailing); domestic names pass through untouched — uppercase
    or long tokens are never eaten."""
    assert split_country_code("eng Arsenal") == ("Arsenal", "eng")
    assert split_country_code("Bayern Munich de") == ("Bayern Munich", "de")
    assert split_country_code("Manchester Utd") == ("Manchester Utd", None)
    assert split_country_code("St Albans") == ("St Albans", None)
    assert split_country_code("Wolves") == ("Wolves", None)


def test_select_covered_games_carries_stage_and_strips_codes():
    """A synthetic European schedule: the covered-tie filter matches through the
    country codes, and each kept game carries its round as stage plus mapped
    countries for auto-create."""
    df = pd.DataFrame(
        [
            {"home_team": "eng Manchester Utd", "away_team": "__ZZ Fremmed__ dk",
             "game_id": "eeee5555", "date": "2025-10-01", "round": "League phase"},
            # foreign vs foreign -> not covered -> dropped
            {"home_team": "es __ZZ Blancos__", "away_team": "de __ZZ Roten__",
             "game_id": "ffff6666", "date": "2025-10-01", "round": "League phase"},
        ]
    )
    with SessionLocal() as session:
        games = select_covered_games(session, df, "2425")
        assert [g["game_id"] for g in games] == ["eeee5555"]
        (game,) = games
        assert game["stage"] == "League phase"
        assert game["home_name"] == "Manchester Utd"
        assert game["away_name"] == "__ZZ Fremmed__"
        assert game["home_country"] == "England"
        assert game["away_country"] == "Denmark"


# --- covered-set union (ADR 0012) -----------------------------------------
# The covered set used to come from team_match alone, i.e. from
# football-data.co.uk. When that source stalls the set silently empties and real
# cup ties are ruled out of scope with nothing logged (observed 2026-08: with E0
# unpublished, no Premier League club was covered for 2627). It is now the union
# of team_match- and fixture-sourced clubs, with divergence reported.


def _synthetic_covered_fixture(session, season: str):
    """A PL fixture in `season` between two teams that have NO team_match rows.

    Synthetic rather than reading the live E0 outage, so these tests keep
    passing once football-data.co.uk publishes again.
    """
    comp = session.scalar(
        select(Competition).where(Competition.name == "Premier League")
    )
    home, away = session.scalars(select(Team).limit(2)).all()
    session.add(
        Fixture(
            competition_id=comp.id,
            season=season,
            date=dt.datetime(2099, 8, 1, tzinfo=dt.timezone.utc),
            home_team_id=home.id,
            away_team_id=away.id,
            status="scheduled",
            stage="",
        )
    )
    session.flush()
    return comp, home, away


def test_covered_team_ids_includes_fixture_sourced_clubs():
    """A club with a PL fixture but no team_match row that season IS covered.

    This is the outage case: fd.co.uk has published nothing, so team_match is
    empty, but the ESPN-sourced fixture proves the club is in the division.
    """
    season = "9899"  # a season with no real data of any kind
    with SessionLocal() as session:
        assert covered_team_ids(session, season) == set()
        _comp, home, away = _synthetic_covered_fixture(session, season)

        covered = covered_team_ids(session, season)

        assert {home.id, away.id} <= covered
        session.rollback()


def test_covered_team_ids_historical_seasons_unchanged():
    """The union is a no-op on a fully-ingested season: wherever team_match
    rows exist, fixtures exist too, so nothing new is pulled into scope."""
    season = "2425"
    with SessionLocal() as session:
        from_team_match = set(
            session.scalars(
                select(TeamMatch.team_id)
                .join(Competition, Competition.id == TeamMatch.competition_id)
                .where(
                    TeamMatch.season == season,
                    Competition.name.in_(["Premier League", "Championship"]),
                )
            )
        )
        assert covered_team_ids(session, season) == from_team_match


def test_covered_divergence_names_the_stalled_source():
    """Divergence reports the competition and both counts, so a source outage
    is visible rather than absorbed into a silently smaller covered set."""
    season = "9899"
    with SessionLocal() as session:
        assert covered_divergence(session, season) == {}
        _synthetic_covered_fixture(session, season)

        divergence = covered_divergence(session, season)

        assert "Premier League" in divergence
        team_match_n, fixture_n = divergence["Premier League"]
        assert team_match_n == 0 and fixture_n == 2
        session.rollback()


def test_covered_team_ids_logs_divergence_when_given_a_log():
    """The log line names the competition and both counts; a run with no
    divergence stays silent."""
    season = "9899"
    with SessionLocal() as session:
        lines: list[str] = []
        covered_team_ids(session, season, log=lines.append)
        assert lines == []

        _synthetic_covered_fixture(session, season)
        covered_team_ids(session, season, log=lines.append)

        assert len(lines) == 1
        assert "Premier League" in lines[0]
        session.rollback()


# --- identity guard: an fbref_id outranks a name ---------------------------
# FA Cup 2627 exposed this: the schedule's "Bournemouth" and "Liverpool" in an
# August qualifying round are the non-league Bournemouth FC (c5b06e34) and AFC
# Liverpool (e84ae6e6). The covered filter matched the PL clubs by NAME, then
# the resolver's name fallback attached the tie to them - two fixtures claiming
# Liverpool and Bournemouth played on 2026-08-08. They did not.


def test_resolver_refuses_a_name_match_whose_fbref_id_conflicts():
    """A stored club with a DIFFERENT fbref_id is a different club, whatever the
    names say. Returning it silently is how the corruption happened."""
    with SessionLocal() as session:
        liverpool = session.scalar(
            select(Team).where(Team.canonical_name == "Liverpool")
        )
        assert liverpool.fbref_id and liverpool.fbref_id != "e84ae6e6"

        # "AFC Liverpool" normalises onto "Liverpool", but carries its own id
        with pytest.raises(UnknownTeamError):
            resolve_or_create_fbref_team(session, "AFC Liverpool", "e84ae6e6")

        session.rollback()


def test_resolver_still_attaches_the_seam_when_the_id_is_unknown():
    """The legitimate case must keep working: a stored club with NO fbref_id
    gets the id attached on a name match (that is the cross-source seam)."""
    with SessionLocal() as session:
        team = Team(canonical_name="Testville Rovers")
        session.add(team)
        session.flush()

        resolved, created = resolve_or_create_fbref_team(
            session, "Testville Rovers", "deadbeef"
        )

        assert created is False
        assert resolved.id == team.id and resolved.fbref_id == "deadbeef"
        session.rollback()


def test_covered_fbref_ids_are_identities_not_names():
    """The covered set expressed as fbref_ids, so coverage can be decided by
    identity once the match page has been read."""
    with SessionLocal() as session:
        ids = covered_fbref_ids(session, "2526")
        team_ids = covered_team_ids(session, "2526")
        expected = {
            t.fbref_id
            for t in session.scalars(select(Team).where(Team.id.in_(team_ids)))
            if t.fbref_id
        }
        assert ids == expected and len(ids) > 0


def test_tie_is_covered_rejects_a_name_collision():
    """Two non-league ids are not our tie, however their names read."""
    with SessionLocal() as session:
        assert tie_is_covered(session, ("c5b06e34", "4119b949"), "2526") is False


def test_tie_is_covered_accepts_a_genuine_covered_side():
    with SessionLocal() as session:
        covered = covered_fbref_ids(session, "2526")
        real = next(iter(covered))
        assert tie_is_covered(session, (real, "4119b949"), "2526") is True


def test_a_skipped_tie_exits_non_zero():
    """The unattended run's only alarm. This path keeps a long backfill alive by
    swallowing per-tie failures, and used to exit 0 regardless - so under
    automation a missed tie was completely silent."""
    assert exit_code({"skipped": ["abc123: UnknownTeamError: ..."]}) == 1


def test_a_clean_run_exits_zero():
    assert exit_code({"skipped": []}) == 0
    assert exit_code({}) == 0


def test_identity_drops_do_not_alarm():
    """A tie that was never ours is not a failure. Alarming on it would fire
    every qualifying round and train the reader to ignore the popup."""
    assert exit_code({"skipped": [], "uncovered": 7}) == 0
