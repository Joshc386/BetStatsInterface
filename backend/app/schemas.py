"""Pydantic models for API responses."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class BreakdownRow(BaseModel):
    date: dt.datetime
    opponent: str | None
    is_home: bool
    value: float | None
    minutes: int | None = None  # players only


class HitRate(BaseModel):
    threshold: float
    direction: str  # "over" | "under"
    hits: int
    n: int
    pct: float


class Summary(BaseModel):
    entity: str  # "team" | "player"
    entity_id: int
    entity_name: str | None
    metric: str
    scope: str
    window: str
    games: int
    total: float | None
    average: float | None
    per_appearance: float | None = None  # players only
    per_90: float | None = None  # players only
    minutes_total: int | None = None  # players only
    hit_rate: HitRate | None
    breakdown: list[BreakdownRow]


class SearchHit(BaseModel):
    entity: str
    id: int
    name: str


class CompetitionOut(BaseModel):
    id: int
    name: str
    type: str
    tier: int | None


class FixtureRow(BaseModel):
    """One team's perspective on a single match — the raw row the Fixture view
    aggregates client-side (see docs/adr/0005)."""

    model_config = ConfigDict(from_attributes=True)

    fixture_id: int
    team_id: int
    date: dt.datetime
    season: str
    competition_id: int
    competition: str
    is_home: bool
    opponent_id: int
    opponent: str
    gf: int | None
    ga: int | None
    shots: int | None
    sot: int | None
    shots_conceded: int | None
    sot_conceded: int | None
    corners: int | None
    fouls: int | None
    yellows: int | None
    reds: int | None
    total_goals: int | None
    btts: bool | None
    clean_sheet: bool | None
    result: str | None


class FixtureComparison(BaseModel):
    """Team form (each team's last-N) + Head-to-Head (both sides of their last-N
    meetings) as raw rows for one fixture pairing."""

    home_id: int
    home_name: str
    away_id: int
    away_name: str
    home: list[FixtureRow]
    away: list[FixtureRow]
    h2h: list[FixtureRow]
