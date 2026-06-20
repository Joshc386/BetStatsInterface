"""Reference tables: competitions, teams, players.

These hold the canonical entities and the per-source identifier columns used to
reconcile FBref / football-data.co.uk / FotMob source names to one canonical id.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, SmallInteger, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import competition_type_enum


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(competition_type_enum, nullable=False)
    country: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[int | None] = mapped_column(SmallInteger)  # English pyramid 1-4
    fbref_key: Mapped[str | None] = mapped_column(Text)
    fdcouk_key: Mapped[str | None] = mapped_column(Text)
    fotmob_id: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("name", name="uq_competitions_name"),)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str | None] = mapped_column(Text)
    # Cross-source reconciliation glue — never join on display names.
    fbref_id: Mapped[str | None] = mapped_column(Text, unique=True)
    fdcouk_name: Mapped[str | None] = mapped_column(Text)
    fotmob_id: Mapped[str | None] = mapped_column(Text, unique=True)
    espn_id: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("canonical_name", name="uq_teams_canonical_name"),
        # One canonical team per football-data.co.uk display name (when present).
        Index(
            "uq_teams_fdcouk_name",
            "fdcouk_name",
            unique=True,
            postgresql_where=text("fdcouk_name IS NOT NULL"),
        ),
    )


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    fbref_id: Mapped[str | None] = mapped_column(Text, unique=True)
    # Reserved for the deferred FotMob player-xG ingestion path (ADR 0001).
    fotmob_id: Mapped[str | None] = mapped_column(Text)
    current_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    nationality: Mapped[str | None] = mapped_column(Text)
    position: Mapped[str | None] = mapped_column(Text)
