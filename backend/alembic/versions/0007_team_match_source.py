"""team_match.source — which source produced this row (docs/adr/0015)

League team data splits by RECENCY, not provider: football-data.co.uk keeps the
historical record while ESPN writes recently-played Fixtures within minutes of
full time. Once more than one writer can produce a `club_league` row, "which
source published this?" stops being answerable from the row's existence.

That question is load-bearing, not bookkeeping. `coverage.find_gaps` tests only
`EXISTS (team_match WHERE fixture_id = ...)`, so its `TEAM_FDCOUK` constant
would be silenced by an ESPN-written row and a football-data.co.uk outage would
be absorbed rather than alarmed — the precise failure ADR 0014 exists to
prevent. This column is what lets the audit keep asking its real question.

Backfill reflects the world before ESPN wrote anything: every `club_league` row
came from football-data.co.uk (`ingestion/team_match.py`), and every cup,
European and international row from FBref (`ingestion/cups.py`,
`ingestion/internationals.py`). NOT NULL, because a row with unknown provenance
is exactly what this column exists to make impossible.

An enum rather than free text, matching competition_type / fixture_status /
match_result: the domain is closed, and a typo'd source silently breaks the
audit rather than failing loudly.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    team_match_source = sa.Enum(
        "fdcouk", "espn", "fbref", name="team_match_source"
    )
    team_match_source.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "team_match",
        sa.Column("source", team_match_source, nullable=True),
    )
    # Provenance as it stood before ESPN could write: league rows are
    # football-data.co.uk's, every other scope is FBref's.
    op.execute(
        "UPDATE team_match SET source = "
        "CASE WHEN competition_type = 'club_league' "
        "THEN 'fdcouk'::team_match_source "
        "ELSE 'fbref'::team_match_source END"
    )
    op.alter_column("team_match", "source", nullable=False)

    # The audit filters on this, per fixture, on every run.
    op.create_index(
        "ix_team_match_source_fixture", "team_match", ["source", "fixture_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_team_match_source_fixture", table_name="team_match")
    op.drop_column("team_match", "source")
    sa.Enum(name="team_match_source").drop(op.get_bind(), checkfirst=True)
