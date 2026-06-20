"""Reconciliation regression tests.

`test_resolve_idempotent` touches the real database in a rolled-back session,
so it leaves no data behind. It requires DATABASE_URL to be reachable.
"""

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
