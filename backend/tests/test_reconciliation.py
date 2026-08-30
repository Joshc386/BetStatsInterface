"""Reconciliation regression tests.

`test_resolve_idempotent` touches the real database in a rolled-back session,
so it leaves no data behind. It requires DATABASE_URL to be reachable.
"""

import datetime as dt

import pytest
from sqlalchemy import select, text

from ingestion.names import clean_name, normalise_for_match
from ingestion.teams import (
    FDCOUK_TEAM_ALIASES,
    UnknownFdcoukTeamError,
    find_duplicate_teams,
    resolve_fdcouk_team,
)
from app.db import SessionLocal
from app.models.reference import Team
from ingestion.upcoming import season_for


def test_clean_name_collapses_whitespace():
    assert clean_name("  Man   United ") == "Man United"
    assert clean_name("Arsenal") == "Arsenal"
    # idempotent
    assert clean_name(clean_name("  Nott'm  Forest ")) == "Nott'm Forest"


def test_normalise_for_match_folds_case_accents_and_suffixes():
    # lowercase + collapse whitespace
    assert normalise_for_match("  Manchester   City ") == "manchester city"
    assert normalise_for_match("ARSENAL") == "arsenal"
    # strip the FC / AFC club tokens (trailing and leading)
    assert normalise_for_match("Arsenal FC") == "arsenal"
    assert normalise_for_match("AFC Bournemouth") == "bournemouth"
    # accent-fold
    assert normalise_for_match("Atlético Madrid") == "atletico madrid"


def test_normalise_for_match_does_not_overmerge_distinct_clubs():
    # the suffix-only fold must never collapse two genuinely different clubs;
    # 'United' vs 'City' is exactly what the alias map (not the normaliser) covers.
    assert normalise_for_match("Manchester United") != normalise_for_match(
        "Manchester City"
    )
    # 'FC' is a club token; a real one-word name must survive intact
    assert normalise_for_match("Liverpool") == "liverpool"


def test_normalise_for_match_is_idempotent():
    once = normalise_for_match("Atlético Madrid FC")
    assert normalise_for_match(once) == once


def test_resolve_fdcouk_team_is_idempotent():
    session = SessionLocal()
    try:
        t1 = resolve_fdcouk_team(
            session, "__Reconciliation Test FC__", allow_create=True
        )
        # whitespace variant must resolve to the SAME canonical row
        t2 = resolve_fdcouk_team(session, "  __Reconciliation Test FC__ ")
        assert t1.id is not None
        assert t1.id == t2.id
        assert t1.fdcouk_name == "__Reconciliation Test FC__"
    finally:
        session.rollback()  # discard the test team
        session.close()


def test_find_duplicate_teams_fires_on_a_normalised_collision():
    """The detector must catch two rows that fold to the same normalised name —
    the silent-split failure ADR 0007 guards against. Rolled back."""
    session = SessionLocal()
    try:
        a = Team(canonical_name="__ZZ Dup City__")
        b = Team(canonical_name="__ZZ Dup City__ FC")  # folds to the same key
        session.add_all([a, b])
        session.flush()
        groups = find_duplicate_teams(session)
        colliding = {tid for _, ids in groups for tid in ids}
        assert a.id in colliding and b.id in colliding
    finally:
        session.rollback()
        session.close()


def test_teams_table_has_no_normalised_name_collisions():
    """Standing regression guard: the live `teams` table holds one row per club.

    A failure means a club has split across two rows (e.g. an auto-created cup
    opponent colliding with a league row) — fix the data, don't relax the test.
    Read-only.
    """
    session = SessionLocal()
    try:
        dupes = find_duplicate_teams(session)
        assert not dupes, f"normalised-name collisions in teams: {dupes}"
    finally:
        session.close()


def test_player_goals_never_exceed_team_goals():
    """Cross-source invariant: a team's summed player goals (FBref) never exceed
    the team's goals for that match (football-data.co.uk).

    FBref credits no player for an opponent's own-goal, so the only correct
    relationship is ``sum(player_goals) <= team_match.gf`` for every team-fixture
    — the gap is own-goals. A row where player goals EXCEED team gf means player
    rows from the wrong match/side contaminated the fixture (the EFL promotion
    play-off natural-key collision). Read-only; touches the real DB.
    """
    session = SessionLocal()
    try:
        violations = session.execute(
            text(
                """
                WITH pg AS (
                  SELECT fixture_id, team_id,
                         SUM(COALESCE(goals, 0)) AS player_goals
                  FROM player_match
                  GROUP BY fixture_id, team_id
                )
                SELECT tm.fixture_id, tm.team_id, pg.player_goals, tm.gf
                FROM team_match tm
                JOIN pg ON pg.fixture_id = tm.fixture_id
                       AND pg.team_id = tm.team_id
                WHERE tm.gf IS NOT NULL AND pg.player_goals > tm.gf
                ORDER BY tm.fixture_id
                """
            )
        ).all()
        assert not violations, (
            f"{len(violations)} team-fixtures where summed player goals exceed "
            f"team gf (data contamination): {[tuple(v) for v in violations[:10]]}"
        )
    finally:
        session.close()


# --- fd.co.uk duplicate-club guard (2026-08-23) ----------------------------


def test_a_relegated_clubs_new_spelling_resolves_to_the_existing_row():
    """football-data.co.uk does not spell clubs the same way across divisions.

    Sheffield Wednesday went down to League One and the E2 CSV calls them
    "Sheffield Wed" where E1 said "Sheffield Weds". Without the alias that minted
    a second club and split their history.
    """
    session = SessionLocal()
    try:
        canonical = resolve_fdcouk_team(session, "Sheffield Weds")
        aliased = resolve_fdcouk_team(session, "Sheffield Wed")
        assert aliased.id == canonical.id
        assert resolve_fdcouk_team(session, "Bradford City").id == (
            resolve_fdcouk_team(session, "Bradford").id
        )
    finally:
        session.rollback()
        session.close()


def test_routine_ingestion_refuses_to_invent_a_club():
    """A name team_match has never seen mid-season is a RENAME, not a new club.
    Auto-creating one is how a club's history splits in silence."""
    session = SessionLocal()
    try:
        with pytest.raises(UnknownFdcoukTeamError, match="never an auto-create"):
            resolve_fdcouk_team(session, "__ZZ Totally New Club__")
    finally:
        session.rollback()
        session.close()


def test_a_respelling_is_refused_even_when_creation_is_allowed():
    """The universe build may create clubs, but not one that shares an existing
    club's first non-generic token — that is a respelling, not a new club."""
    session = SessionLocal()
    try:
        existing = Team(canonical_name="__ZZQuux Rovers__", fdcouk_name="__ZZQuux Rovers__")
        session.add(existing)
        session.flush()
        with pytest.raises(UnknownFdcoukTeamError, match="spelled two ways"):
            resolve_fdcouk_team(session, "__ZZQuux Rovers__ City", allow_create=True)
    finally:
        session.rollback()
        session.close()


def test_the_universe_build_may_still_create_a_genuinely_new_club():
    """The guard must not break the bootstrap, which legitimately creates every
    club it sees from the CSV."""
    session = SessionLocal()
    try:
        team = resolve_fdcouk_team(session, "__ZZ Unrelated Wanderers__", allow_create=True)
        assert team.id is not None
        assert team.fdcouk_name == "__ZZ Unrelated Wanderers__"
    finally:
        session.rollback()
        session.close()


def test_every_alias_target_is_a_club_we_actually_hold():
    """A typo in the alias map would route a real club to nothing. Read-only."""
    session = SessionLocal()
    try:
        for source, target in FDCOUK_TEAM_ALIASES.items():
            found = session.scalar(select(Team).where(Team.fdcouk_name == target))
            assert found is not None, f"alias {source!r} -> {target!r} matches no team"
    finally:
        session.close()


def test_no_phantom_clubs_carry_current_season_data():
    """Standing regression guard for the 2026-08-23 duplicate-club bug.

    A club we genuinely track always carries a source id — an `espn_id` from the
    fixture slate, an `fbref_id` from the identity spine, or both. A row with
    NEITHER that nonetheless holds current-season team data was auto-created by a
    source spelling an existing club differently, and has silently taken a real
    club's matches with it.

    Deliberately scoped to the CURRENT season: clubs long outside the four tiers
    (Southend, Scunthorpe) legitimately have no ids and old rows, and flagging
    them forever would make this guard noise.

    Chosen over a first-token collision check, which was measured and rejected:
    all 8 collisions in the live table (Man City/United, Sheffield United/Weds,
    Bristol City/Rvs, Korea Republic/DPR, ...) are genuinely different clubs with
    distinct ids, so that check is 100% false positives.
    """
    session = SessionLocal()
    try:
        season = season_for(dt.datetime.now(dt.timezone.utc))
        rows = session.execute(
            text(
                """
                SELECT t.id, t.canonical_name
                FROM teams t
                WHERE t.espn_id IS NULL AND t.fbref_id IS NULL
                  AND EXISTS (
                    SELECT 1 FROM team_match tm
                    WHERE tm.team_id = t.id AND tm.season = :season
                  )
                """
            ),
            {"season": season},
        ).all()
        assert not rows, (
            f"phantom club(s) holding {season} data: {rows} — a source spelled an "
            f"existing club differently. Add a FDCOUK_TEAM_ALIASES entry and merge."
        )
    finally:
        session.close()


def test_an_adoptable_club_is_told_the_remedy_that_actually_works():
    """A club auto-created by the CUP path carries fbref_id but no fdcouk_name.
    When football-data.co.uk later serves that exact name, the lookup misses and
    the token guard fires — correctly, since the club is already held.

    But the generic guidance ("add a FDCOUK_TEAM_ALIASES entry") is a dead end
    here: the names are IDENTICAL, so the alias maps the name to itself, the
    lookup is still on fdcouk_name, and it still misses. Verified against the
    real 2026-08 case (Boreham Wood, created by the FA Cup path) — only setting
    fdcouk_name on the existing row resolves it.

    So the adoptable case must be named separately and told what to do.
    """
    session = SessionLocal()
    try:
        held = Team(canonical_name="__ZZAdopt Wood__", fbref_id="__zzadopt__")
        session.add(held)
        session.flush()
        assert held.fdcouk_name is None

        with pytest.raises(UnknownFdcoukTeamError, match="fdcouk_name") as exc:
            resolve_fdcouk_team(session, "__ZZAdopt Wood__")

        message = str(exc.value)
        assert str(held.id) in message          # names the row to fix
        assert "FDCOUK_TEAM_ALIASES" not in message   # not the dead-end remedy
    finally:
        session.rollback()
        session.close()
