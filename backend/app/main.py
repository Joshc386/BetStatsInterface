"""Read-only FastAPI over Postgres. Serves Summary Metrics for teams and players.

No external source is ever touched here — the API reads only from the local DB.
Team data is live; player endpoints work identically and fill in once player_match
is populated (pending FBref access).
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.facts import PlayerMatch, TeamMatch
from app.models.reference import Competition, Player, Team
from app.fixtures import fixture_comparison, fixture_detail
from app.schemas import (
    CompetitionOut,
    FixtureComparison,
    FixtureRow,
    SearchHit,
    Summary,
)
from app.stats import entity_summary, registry

app = FastAPI(title="BetStats Research API", version="0.1.0")

# Single-user, local-only tool: the Vite dev server (5173) is the sole browser
# client. Allow localhost origins so the read-only UI can call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

SCOPES = ("club_league", "club_cup", "club_european", "international")


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@app.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    return {
        "status": "ok",
        "teams": session.scalar(select(func.count()).select_from(Team)),
        "team_match": session.scalar(select(func.count()).select_from(TeamMatch)),
        "player_match": session.scalar(select(func.count()).select_from(PlayerMatch)),
    }


@app.get("/metrics")
def metrics() -> dict:
    return {
        "team": sorted(registry("team")),
        "player": sorted(registry("player")),
        "scopes": list(SCOPES),
    }


@app.get("/competitions", response_model=list[CompetitionOut])
def competitions(session: Session = Depends(get_session)) -> list[CompetitionOut]:
    rows = session.execute(
        select(Competition.id, Competition.name, Competition.type, Competition.tier)
        .order_by(Competition.tier, Competition.name)
    ).all()
    return [CompetitionOut(id=i, name=n, type=t, tier=tier) for i, n, t, tier in rows]


@app.get("/search", response_model=list[SearchHit])
def search(q: str = Query(min_length=2), limit: int = 20,
           session: Session = Depends(get_session)) -> list[SearchHit]:
    like = f"%{q}%"
    teams = session.execute(
        select(Team.id, Team.canonical_name).where(Team.canonical_name.ilike(like)).limit(limit)
    ).all()
    players = session.execute(
        select(Player.id, Player.canonical_name).where(Player.canonical_name.ilike(like)).limit(limit)
    ).all()
    return (
        [SearchHit(entity="team", id=i, name=n) for i, n in teams]
        + [SearchHit(entity="player", id=i, name=n) for i, n in players]
    )


def _summary(entity, entity_id, metric, n, competition_id, scope, season, threshold,
             direction, window_mode, session, team_id=None) -> Summary:
    if metric not in registry(entity):
        raise HTTPException(404, f"unknown {entity} metric '{metric}'. See /metrics.")
    if scope is not None and scope not in SCOPES:
        raise HTTPException(422, f"unknown scope '{scope}'. One of {SCOPES}.")
    if competition_id is not None and session.get(Competition, competition_id) is None:
        raise HTTPException(404, f"competition {competition_id} not found")
    result = entity_summary(
        session, entity=entity, entity_id=entity_id, metric=metric, n=n,
        competition_id=competition_id, scope=scope, team_id=team_id,
        seasons=[season] if season else None, threshold=threshold,
        direction=direction, window_mode=window_mode,
    )
    if result["entity_name"] is None:
        raise HTTPException(404, f"{entity} {entity_id} not found")
    return Summary(**result)


@app.get("/teams/{team_id}/summary", response_model=Summary)
def team_summary(
    team_id: int,
    metric: str = "btts",
    n: int = Query(10, ge=1, le=100),
    competition_id: int | None = Query(None, description="specific competition; omit for all"),
    scope: str | None = None,
    season: str | None = None,
    threshold: float | None = None,
    direction: str = Query("over", pattern="^(over|under)$"),
    window_mode: str = Query("display", pattern="^(display|going_in)$"),
    session: Session = Depends(get_session),
) -> Summary:
    return _summary("team", team_id, metric, n, competition_id, scope, season,
                    threshold, direction, window_mode, session)


@app.get("/players/{player_id}/summary", response_model=Summary)
def player_summary(
    player_id: int,
    metric: str = "shots_on_target",
    n: int = Query(10, ge=1, le=100),
    competition_id: int | None = Query(None, description="specific competition; omit for all"),
    scope: str | None = None,
    season: str | None = None,
    team_id: int | None = Query(None, description="isolate one club Spell (players only)"),
    threshold: float | None = None,
    direction: str = Query("over", pattern="^(over|under)$"),
    window_mode: str = Query("display", pattern="^(display|going_in)$"),
    session: Session = Depends(get_session),
) -> Summary:
    return _summary("player", player_id, metric, n, competition_id, scope, season,
                    threshold, direction, window_mode, session, team_id=team_id)


@app.get("/fixtures/compare", response_model=FixtureComparison)
def fixtures_compare(
    home: int = Query(..., description="home team id"),
    away: int = Query(..., description="away team id"),
    n: int = Query(10, ge=1, le=50),
    session: Session = Depends(get_session),
) -> FixtureComparison:
    """Raw rows for the Fixture view — each team's last-n league games plus both
    sides of their last-n league meetings. The client aggregates (ADR 0005)."""
    teams = {}
    for team_id in (home, away):
        team = session.get(Team, team_id)
        if team is None:
            raise HTTPException(404, f"team {team_id} not found")
        teams[team_id] = team.canonical_name
    blocks = fixture_comparison(session, home_id=home, away_id=away, n=n)
    return FixtureComparison(
        home_id=home,
        home_name=teams[home],
        away_id=away,
        away_name=teams[away],
        home=[FixtureRow.model_validate(r) for r in blocks["home"]],
        away=[FixtureRow.model_validate(r) for r in blocks["away"]],
        h2h=[FixtureRow.model_validate(r) for r in blocks["h2h"]],
    )


@app.get("/fixtures/{fixture_id}", response_model=list[FixtureRow])
def fixture_drilldown(
    fixture_id: int, session: Session = Depends(get_session)
) -> list[FixtureRow]:
    """Both teams' full team_match rows for one match (the drill-down)."""
    rows = fixture_detail(session, fixture_id=fixture_id)
    if not rows:
        raise HTTPException(404, f"fixture {fixture_id} not found")
    return [FixtureRow.model_validate(r) for r in rows]
