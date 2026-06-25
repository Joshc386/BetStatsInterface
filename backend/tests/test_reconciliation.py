"""Reconciliation regression tests.

`test_resolve_idempotent` touches the real database in a rolled-back session,
so it leaves no data behind. It requires DATABASE_URL to be reachable.
"""

from sqlalchemy import text

from ingestion.names import clean_name
from ingestion.teams import resolve_fdcouk_team
from app.db import SessionLocal


def test_clean_name_collapses_whitespace():
    assert clean_name("  Man   United ") == "Man United"
    assert clean_name("Arsenal") == "Arsenal"
    # idempotent
    assert clean_name(clean_name("  Nott'm  Forest ")) == "Nott'm Forest"


def test_resolve_fdcouk_team_is_idempotent():
    session = SessionLocal()
    try:
        t1 = resolve_fdcouk_team(session, "__Reconciliation Test FC__")
        # whitespace variant must resolve to the SAME canonical row
        t2 = resolve_fdcouk_team(session, "  __Reconciliation Test FC__ ")
        assert t1.id is not None
        assert t1.id == t2.id
        assert t1.fdcouk_name == "__Reconciliation Test FC__"
    finally:
        session.rollback()  # discard the test team
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
