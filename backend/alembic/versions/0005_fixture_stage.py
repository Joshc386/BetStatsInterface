"""fixtures.stage — stage/round discriminator in the fixture natural key

European competitions break the old invariant that a club never hosts the same
opponent twice in one competition-season (UCL league phase + knockout leg, and
pre-2024 group + knockout rematches; see docs/adr/0011). `stage` — the FBref
schedule round ("League phase", "Round of 16", …) — joins the natural key so
those are distinct fixtures. Default '' keeps league/domestic-cup/upcoming
ingestion byte-identical: they never set it, and the recreated constraint keeps
its name, so team_match.py's ON CONFLICT upsert is untouched.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fixtures",
        sa.Column("stage", sa.Text(), nullable=False, server_default=""),
    )
    op.drop_constraint("uq_fixture_natural", "fixtures", type_="unique")
    op.create_unique_constraint(
        "uq_fixture_natural",
        "fixtures",
        ["competition_id", "season", "home_team_id", "away_team_id", "stage"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_fixture_natural", "fixtures", type_="unique")
    op.create_unique_constraint(
        "uq_fixture_natural",
        "fixtures",
        ["competition_id", "season", "home_team_id", "away_team_id"],
    )
    op.drop_column("fixtures", "stage")
