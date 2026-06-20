"""reference natural keys

Adds natural unique keys needed for idempotent reconciliation upserts:
competitions.name, teams.canonical_name, and a partial unique index on
teams.fdcouk_name (unique only where present).

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint("uq_competitions_name", "competitions", ["name"])
    op.create_unique_constraint(
        "uq_teams_canonical_name", "teams", ["canonical_name"]
    )
    op.create_index(
        "uq_teams_fdcouk_name",
        "teams",
        ["fdcouk_name"],
        unique=True,
        postgresql_where=sa.text("fdcouk_name IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_teams_fdcouk_name",
        table_name="teams",
        postgresql_where=sa.text("fdcouk_name IS NOT NULL"),
    )
    op.drop_constraint("uq_teams_canonical_name", "teams", type_="unique")
    op.drop_constraint("uq_competitions_name", "competitions", type_="unique")
