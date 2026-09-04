"""Pydantic models for API responses."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class BreakdownRow(BaseModel):
    date: dt.datetime
    opponent_id: int | None = None
    opponent: str | None
    is_home: bool
    value: float | None
    # The team's W/D/L for that match. For a player it is his club's result,
    # read across from team_match — NULL when no team row exists (a player pass
    # run without its team pass), so the UI must tolerate its absence.
    result: str | None = None
    minutes: int | None = None  # players only
    # players only — the club + competition for that appearance (Spell grouping)
    team_id: int | None = None
    team: str | None = None
    competition_id: int | None = None
    competition: str | None = None


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
    average: float | None = None  # teams only (a player's is per_appearance)
    per_appearance: float | None = None  # players only
    per_90: float | None = None  # players only
    minutes_total: int | None = None  # players only
    # The Recorded Appearance counts the aggregates actually divided by, so the
    # figures above are checkable against them by hand (ADR 0016). `games` and
    # `minutes_total` remain metric-independent window facts.
    recorded_games: int
    recorded_minutes: int | None = None  # players only
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


class SquadMember(BaseModel):
    """One member of a club's Squad (see docs/adr/0013)."""

    model_config = ConfigDict(from_attributes=True)

    player_id: int
    player: str
    # His most-recent appearance FOR THIS CLUB. None when we hold none — a new
    # signing is in the Squad and unknown to us, which the panel shows as "—".
    last_seen: dt.datetime | None = None


class SquadAppearanceRow(BaseModel):
    """One member's appearance at the club — the raw row the client aggregates."""

    model_config = ConfigDict(from_attributes=True)

    player_id: int
    player: str
    date: dt.datetime
    season: str
    competition_id: int
    competition: str
    competition_type: str
    opponent_id: int
    opponent: str
    is_home: bool
    minutes: int | None
    goals: int | None
    assists: int | None
    shots: int | None
    sot: int | None
    tackles: int | None
    fouls_drawn: int | None
    fouls_committed: int | None
    yellows: int | None
    reds: int | None
    second_yellows: int | None
    carded: bool | None


class SquadForm(BaseModel):
    """A club's Squad plus each member's raw appearance rows at the club.
    The client windows + aggregates the rows per player (docs/adr/0006, 0013)."""

    team_id: int
    team_name: str
    # "squad" = the ESPN roster ∪ the last 30 days; "recent" = the appearance-
    # derived fallback, for a club with no roster. The panel labels itself from
    # this rather than implying a registered squad it does not have.
    membership: str
    members: list[SquadMember]
    rows: list[SquadAppearanceRow]


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


class TableRow(BaseModel):
    """One club's line in a computed league table (ADR 0010). `adjustment` is
    the season's administrative points change (negative = deduction), already
    included in `points`; `adjustment_note` says why."""

    position: int
    team_id: int
    team_name: str
    played: int
    won: int
    drawn: int
    lost: int
    gf: int
    ga: int
    gd: int
    points: int
    adjustment: int
    adjustment_note: str | None


class UpcomingFixture(BaseModel):
    """One scheduled fixture from the upcoming feed (ADR 0009) — display-only,
    no stats; links into the Fixture view by team pair."""

    model_config = ConfigDict(from_attributes=True)

    fixture_id: int
    date: dt.datetime
    competition: str
    home_id: int
    home_name: str
    away_id: int
    away_name: str
