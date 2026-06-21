"""Pydantic models for API responses."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


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


class H2HRow(BaseModel):
    date: dt.datetime
    season: str
    is_home: bool
    gf: int | None
    ga: int | None
    result: str | None
