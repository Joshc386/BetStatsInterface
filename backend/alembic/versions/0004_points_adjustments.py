"""points_adjustments — administrative deductions for the computed league table

A computed table derives everything from team_match except administrative
points changes (PSR/insolvency deductions). One row per club-season, seeded
from the ESPN standings feed (docs/adr/0010).

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "points_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "competition_id",
            sa.Integer(),
            sa.ForeignKey("competitions.id"),
            nullable=False,
        ),
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("points", sa.SmallInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "competition_id", "season", "team_id", name="uq_points_adjustment"
        ),
    )


def downgrade() -> None:
    op.drop_table("points_adjustments")
