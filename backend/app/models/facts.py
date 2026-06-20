"""Schedule + fact tables.

`team_match` / `player_match` are one row per entity per Fixture — the per-game
row IS the breakdown; rolling Summary Metrics are computed at query time, never
stored here. Derived booleans are Postgres GENERATED columns so they cannot drift
from their inputs.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import (
    competition_type_enum,
    fixture_status_enum,
    match_result_enum,
)


class Fixture(Base):
    """A single scheduled event between two teams — upcoming or finished."""

    __tablename__ = "fixtures"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(
        ForeignKey("competitions.id"), nullable=False
    )
    season: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    status: Mapped[str] = mapped_column(fixture_status_enum, nullable=False)
    fbref_match_id: Mapped[str | None] = mapped_column(Text)
    fdcouk_ref: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "competition_id",
            "season",
            "home_team_id",
            "away_team_id",
            name="uq_fixture_natural",
        ),
        Index(
            "uq_fixtures_fbref_match_id",
            "fbref_match_id",
            unique=True,
            postgresql_where=text("fbref_match_id IS NOT NULL"),
        ),
        Index("ix_fixtures_date", "date"),
        Index("ix_fixtures_status_date", "status", "date"),
        Index("ix_fixtures_comp_season", "competition_id", "season"),
    )


class TeamMatch(Base):
    """One team's perspective on a Fixture. Exactly two per Fixture."""

    __tablename__ = "team_match"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), nullable=False)
    competition_id: Mapped[int] = mapped_column(
        ForeignKey("competitions.id"), nullable=False
    )
    competition_type: Mapped[str] = mapped_column(
        competition_type_enum, nullable=False
    )
    season: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    opponent_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)

    gf: Mapped[int | None] = mapped_column(SmallInteger)
    ga: Mapped[int | None] = mapped_column(SmallInteger)
    shots: Mapped[int | None] = mapped_column(SmallInteger)
    sot: Mapped[int | None] = mapped_column(SmallInteger)
    shots_conceded: Mapped[int | None] = mapped_column(SmallInteger)
    sot_conceded: Mapped[int | None] = mapped_column(SmallInteger)
    fouls: Mapped[int | None] = mapped_column(SmallInteger)
    corners: Mapped[int | None] = mapped_column(SmallInteger)
    yellows: Mapped[int | None] = mapped_column(SmallInteger)
    reds: Mapped[int | None] = mapped_column(SmallInteger)
    xg: Mapped[float | None] = mapped_column(Numeric(5, 2))  # FotMob; null if uncovered

    # Generated (STORED) — cannot drift from inputs.
    clean_sheet: Mapped[bool | None] = mapped_column(
        Boolean, Computed("ga = 0", persisted=True)
    )
    btts: Mapped[bool | None] = mapped_column(
        Boolean, Computed("gf > 0 AND ga > 0", persisted=True)
    )
    total_goals: Mapped[int | None] = mapped_column(
        SmallInteger, Computed("gf + ga", persisted=True)
    )
    result: Mapped[str | None] = mapped_column(
        match_result_enum,
        Computed(
            "CASE WHEN gf > ga THEN 'W'::match_result "
            "WHEN gf < ga THEN 'L'::match_result "
            "WHEN gf = ga THEN 'D'::match_result END",
            persisted=True,
        ),
    )

    __table_args__ = (
        UniqueConstraint("fixture_id", "team_id", name="uq_team_match"),
        Index("ix_team_match_team_scope_date", "team_id", "competition_type", "date"),
        Index("ix_team_match_h2h", "team_id", "opponent_id", "date"),
        Index("ix_team_match_team_season", "team_id", "season"),
    )


class PlayerMatch(Base):
    """One player's perspective on a Fixture — i.e. an Appearance (minutes > 0)."""

    __tablename__ = "player_match"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), nullable=False)
    competition_id: Mapped[int] = mapped_column(
        ForeignKey("competitions.id"), nullable=False
    )
    competition_type: Mapped[str] = mapped_column(
        competition_type_enum, nullable=False
    )
    season: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    opponent_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)

    minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    shots: Mapped[int | None] = mapped_column(SmallInteger)
    sot: Mapped[int | None] = mapped_column(SmallInteger)
    tackles: Mapped[int | None] = mapped_column(SmallInteger)
    fouls_drawn: Mapped[int | None] = mapped_column(SmallInteger)
    fouls_committed: Mapped[int | None] = mapped_column(SmallInteger)
    yellows: Mapped[int | None] = mapped_column(SmallInteger)
    reds: Mapped[int | None] = mapped_column(SmallInteger)
    second_yellows: Mapped[int | None] = mapped_column(SmallInteger)  # FBref 2CrdY
    xg: Mapped[float | None] = mapped_column(Numeric(5, 2))  # NULL in v1 (no source)

    # Generated (STORED) — drives the "to be booked" hit-rate.
    carded: Mapped[bool | None] = mapped_column(
        Boolean,
        Computed(
            "(COALESCE(yellows, 0) > 0 OR COALESCE(reds, 0) > 0)", persisted=True
        ),
    )

    __table_args__ = (
        UniqueConstraint("fixture_id", "player_id", name="uq_player_match"),
        Index(
            "ix_player_match_player_scope_date",
            "player_id",
            "competition_type",
            "date",
        ),
        Index("ix_player_match_player_season", "player_id", "season"),
        Index("ix_player_match_team_date", "team_id", "date"),
    )


class Squad(Base):
    """Current roster snapshot per club, refreshed by the roster job."""

    __tablename__ = "squads"

    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_seen: Mapped[dt.date | None] = mapped_column(Date)
