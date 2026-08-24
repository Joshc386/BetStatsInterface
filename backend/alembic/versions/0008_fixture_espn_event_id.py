"""fixtures.espn_event_id — the handle for ESPN's per-match summary (docs/adr/0015)

ESPN-sourced Team-Match rows come from `/summary?event={id}`, so the write path
needs that id. It could be re-derived by fetching the scoreboard for the
fixture's date and matching on `teams.espn_id`, but that spends a request
rediscovering an identity `upcoming` already held in its hand — and this project
resolves an identity ONCE and stamps it, then never joins on anything else
(teams.espn_id, players.espn_id, fixtures.fbref_match_id all follow this shape).

Nullable and not unique, exactly like `fbref_match_id`: only the competitions on
ESPN's scoreboard ever carry one, and historical fixtures created before this
column existed keep NULL. Stamped whenever `upsert_event` actually writes —
created, updated or promoted to finished — which is always before the match is
played, so a Fixture that matters here has its id well before kick-off.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fixtures", sa.Column("espn_event_id", sa.Text(), nullable=True)
    )
    op.create_index(
        "ix_fixtures_espn_event_id", "fixtures", ["espn_event_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_fixtures_espn_event_id", table_name="fixtures")
    op.drop_column("fixtures", "espn_event_id")
