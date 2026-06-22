"""player_match goals + assists

FBref's `summary` stat type provides goals (Gls) and assists (Ast) for free, so
add them to player_match ahead of FBref ingestion.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("player_match", sa.Column("goals", sa.SmallInteger()))
    op.add_column("player_match", sa.Column("assists", sa.SmallInteger()))


def downgrade() -> None:
    op.drop_column("player_match", "assists")
    op.drop_column("player_match", "goals")
