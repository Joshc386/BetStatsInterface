"""referees + fixtures.referee_id — the Referee hub (docs/adr/0018)

A Referee is the name his match page prints (no source gives an id), so the
table holds one canonical name per man; one man printed two ways is folded by
the hand-confirmed alias list in ingestion/referees.py, never here.

`fixtures.referee_id` is nullable: a page can name no referee (25 of 13,501
league and cup pages do), and unplayed Fixtures have none by definition — the
Appointed referee is URL-only and never stored (CONTEXT.md). Indexed with
`date` because every referee query is "his Fixtures, newest first".

Additive only: no existing row is touched; the backfill fills the column.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "referees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.UniqueConstraint("name", name="uq_referees_name"),
    )
    op.add_column(
        "fixtures",
        sa.Column(
            "referee_id",
            sa.Integer(),
            sa.ForeignKey("referees.id", name="fk_fixtures_referee_id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_fixtures_referee_date", "fixtures", ["referee_id", "date"]
    )


def downgrade() -> None:
    op.drop_index("ix_fixtures_referee_date", table_name="fixtures")
    op.drop_column("fixtures", "referee_id")
    op.drop_table("referees")
