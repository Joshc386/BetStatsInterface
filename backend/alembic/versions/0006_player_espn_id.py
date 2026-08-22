"""players.espn_id — the cross-source handle for ESPN roster reconciliation

Squad membership now comes from each club's ESPN roster (docs/adr/0013), which
means matching ESPN athletes to our players. Names alone are not a safe join
(Guðjohnsen/Gudjohnsen, Dapo/Oladapo Afolayan), so the roster job resolves once
via a deterministic ladder and then stamps this id — after which matching never
depends on spelling again. Exactly the teams.espn_id pattern from ADR 0009.

Nullable, and NOT unique: teams.espn_id is not unique either, and a partial
index is the wrong tool here — most players will never carry one (only the four
English tiers have rosters). Indexed because the roster job looks players up by
it on every run.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("players", sa.Column("espn_id", sa.Text(), nullable=True))
    op.create_index("ix_players_espn_id", "players", ["espn_id"])


def downgrade() -> None:
    op.drop_index("ix_players_espn_id", table_name="players")
    op.drop_column("players", "espn_id")
