"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-19

Creates reference tables (competitions, teams, players), the fixtures schedule,
and the team_match / player_match fact tables with GENERATED derived columns.
No odds table (out of scope). See docs/phase-1-schema-plan.md.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- enum types -------------------------------------------------------
    competition_type = postgresql.ENUM(
        "club_league",
        "club_cup",
        "club_european",
        "international",
        name="competition_type",
    )
    fixture_status = postgresql.ENUM("scheduled", "finished", name="fixture_status")
    match_result = postgresql.ENUM("W", "D", "L", name="match_result")
    competition_type.create(op.get_bind(), checkfirst=True)
    fixture_status.create(op.get_bind(), checkfirst=True)
    match_result.create(op.get_bind(), checkfirst=True)

    # References to the (now-created) enum types for use in columns.
    ct = postgresql.ENUM(name="competition_type", create_type=False)
    fs = postgresql.ENUM(name="fixture_status", create_type=False)
    mr = postgresql.ENUM(name="match_result", create_type=False)

    # --- reference tables -------------------------------------------------
    op.create_table(
        "competitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", ct, nullable=False),
        sa.Column("country", sa.Text()),
        sa.Column("tier", sa.SmallInteger()),
        sa.Column("fbref_key", sa.Text()),
        sa.Column("fdcouk_key", sa.Text()),
        sa.Column("fotmob_id", sa.Text()),
    )

    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("country", sa.Text()),
        sa.Column("fbref_id", sa.Text(), unique=True),
        sa.Column("fdcouk_name", sa.Text()),
        sa.Column("fotmob_id", sa.Text(), unique=True),
        sa.Column("espn_id", sa.Text()),
    )

    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("fbref_id", sa.Text(), unique=True),
        sa.Column("fotmob_id", sa.Text()),
        sa.Column("current_team_id", sa.Integer(), sa.ForeignKey("teams.id")),
        sa.Column("nationality", sa.Text()),
        sa.Column("position", sa.Text()),
    )

    # --- schedule ---------------------------------------------------------
    op.create_table(
        "fixtures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "competition_id",
            sa.Integer(),
            sa.ForeignKey("competitions.id"),
            nullable=False,
        ),
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "home_team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False
        ),
        sa.Column(
            "away_team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False
        ),
        sa.Column("status", fs, nullable=False),
        sa.Column("fbref_match_id", sa.Text()),
        sa.Column("fdcouk_ref", sa.Text()),
        sa.UniqueConstraint(
            "competition_id",
            "season",
            "home_team_id",
            "away_team_id",
            name="uq_fixture_natural",
        ),
    )
    op.create_index(
        "uq_fixtures_fbref_match_id",
        "fixtures",
        ["fbref_match_id"],
        unique=True,
        postgresql_where=sa.text("fbref_match_id IS NOT NULL"),
    )
    op.create_index("ix_fixtures_date", "fixtures", ["date"])
    op.create_index("ix_fixtures_status_date", "fixtures", ["status", "date"])
    op.create_index(
        "ix_fixtures_comp_season", "fixtures", ["competition_id", "season"]
    )

    # --- team_match -------------------------------------------------------
    op.create_table(
        "team_match",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "fixture_id", sa.Integer(), sa.ForeignKey("fixtures.id"), nullable=False
        ),
        sa.Column(
            "competition_id",
            sa.Integer(),
            sa.ForeignKey("competitions.id"),
            nullable=False,
        ),
        sa.Column("competition_type", ct, nullable=False),
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column(
            "opponent_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False
        ),
        sa.Column("is_home", sa.Boolean(), nullable=False),
        sa.Column("gf", sa.SmallInteger()),
        sa.Column("ga", sa.SmallInteger()),
        sa.Column("shots", sa.SmallInteger()),
        sa.Column("sot", sa.SmallInteger()),
        sa.Column("shots_conceded", sa.SmallInteger()),
        sa.Column("sot_conceded", sa.SmallInteger()),
        sa.Column("fouls", sa.SmallInteger()),
        sa.Column("corners", sa.SmallInteger()),
        sa.Column("yellows", sa.SmallInteger()),
        sa.Column("reds", sa.SmallInteger()),
        sa.Column("xg", sa.Numeric(5, 2)),
        sa.Column(
            "clean_sheet",
            sa.Boolean(),
            sa.Computed("ga = 0", persisted=True),
        ),
        sa.Column(
            "btts",
            sa.Boolean(),
            sa.Computed("gf > 0 AND ga > 0", persisted=True),
        ),
        sa.Column(
            "total_goals",
            sa.SmallInteger(),
            sa.Computed("gf + ga", persisted=True),
        ),
        sa.Column(
            "result",
            mr,
            sa.Computed(
                "CASE WHEN gf > ga THEN 'W'::match_result "
                "WHEN gf < ga THEN 'L'::match_result "
                "WHEN gf = ga THEN 'D'::match_result END",
                persisted=True,
            ),
        ),
        sa.UniqueConstraint("fixture_id", "team_id", name="uq_team_match"),
    )
    op.create_index(
        "ix_team_match_team_scope_date",
        "team_match",
        ["team_id", "competition_type", "date"],
    )
    op.create_index(
        "ix_team_match_h2h", "team_match", ["team_id", "opponent_id", "date"]
    )
    op.create_index(
        "ix_team_match_team_season", "team_match", ["team_id", "season"]
    )

    # --- player_match -----------------------------------------------------
    op.create_table(
        "player_match",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "fixture_id", sa.Integer(), sa.ForeignKey("fixtures.id"), nullable=False
        ),
        sa.Column(
            "competition_id",
            sa.Integer(),
            sa.ForeignKey("competitions.id"),
            nullable=False,
        ),
        sa.Column("competition_type", ct, nullable=False),
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False
        ),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column(
            "opponent_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False
        ),
        sa.Column("is_home", sa.Boolean(), nullable=False),
        sa.Column("minutes", sa.SmallInteger(), nullable=False),
        sa.Column("shots", sa.SmallInteger()),
        sa.Column("sot", sa.SmallInteger()),
        sa.Column("tackles", sa.SmallInteger()),
        sa.Column("fouls_drawn", sa.SmallInteger()),
        sa.Column("fouls_committed", sa.SmallInteger()),
        sa.Column("yellows", sa.SmallInteger()),
        sa.Column("reds", sa.SmallInteger()),
        sa.Column("second_yellows", sa.SmallInteger()),
        sa.Column("xg", sa.Numeric(5, 2)),
        sa.Column(
            "carded",
            sa.Boolean(),
            sa.Computed(
                "(COALESCE(yellows, 0) > 0 OR COALESCE(reds, 0) > 0)",
                persisted=True,
            ),
        ),
        sa.UniqueConstraint("fixture_id", "player_id", name="uq_player_match"),
    )
    op.create_index(
        "ix_player_match_player_scope_date",
        "player_match",
        ["player_id", "competition_type", "date"],
    )
    op.create_index(
        "ix_player_match_player_season", "player_match", ["player_id", "season"]
    )
    op.create_index(
        "ix_player_match_team_date", "player_match", ["team_id", "date"]
    )

    # --- squads -----------------------------------------------------------
    op.create_table(
        "squads",
        sa.Column(
            "team_id", sa.Integer(), sa.ForeignKey("teams.id"), primary_key=True
        ),
        sa.Column(
            "player_id", sa.Integer(), sa.ForeignKey("players.id"), primary_key=True
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_seen", sa.Date()),
    )


def downgrade() -> None:
    op.drop_table("squads")
    op.drop_table("player_match")
    op.drop_table("team_match")
    op.drop_table("fixtures")
    op.drop_table("players")
    op.drop_table("teams")
    op.drop_table("competitions")
    for name in ("match_result", "fixture_status", "competition_type"):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
