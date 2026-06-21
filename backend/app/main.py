"""Read-only FastAPI over Postgres. Serves Summary Metrics for teams and players.

No external source is ever touched here — the API reads only from the local DB.
Team data is live; player endpoints work identically and fill in once player_match
is populated (pending FBref access).
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.facts import PlayerMatch, TeamMatch
from app.models.reference import Player, Team
from app.schemas import H2HRow, SearchHit, Summary
from app.stats import entity_summary, registry

app = FastAPI(title="BetStats Research API", version="0.1.0")

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


def _summary(entity, entity_id, metric, n, scope, season, threshold, direction,
             window_mode, session) -> Summary:
    if metric not in registry(entity):
        raise HTTPException(404, f"unknown {entity} metric '{metric}'. See /metrics.")
    if scope not in SCOPES:
        raise HTTPException(422, f"unknown scope '{scope}'. One of {SCOPES}.")
    result = entity_summary(
        session, entity=entity, entity_id=entity_id, metric=metric, n=n, scope=scope,
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
    scope: str = "club_league",
    season: str | None = None,
    threshold: float | None = None,
    direction: str = Query("over", pattern="^(over|under)$"),
    window_mode: str = Query("display", pattern="^(display|going_in)$"),
    session: Session = Depends(get_session),
) -> Summary:
    return _summary("team", team_id, metric, n, scope, season, threshold,
                    direction, window_mode, session)


@app.get("/players/{player_id}/summary", response_model=Summary)
def player_summary(
    player_id: int,
    metric: str = "shots_on_target",
    n: int = Query(10, ge=1, le=100),
    scope: str = "club_league",
    season: str | None = None,
    threshold: float | None = None,
    direction: str = Query("over", pattern="^(over|under)$"),
    window_mode: str = Query("display", pattern="^(display|going_in)$"),
    session: Session = Depends(get_session),
) -> Summary:
    return _summary("player", player_id, metric, n, scope, season, threshold,
                    direction, window_mode, session)


@app.get("/teams/{team_id}/h2h/{opponent_id}", response_model=list[H2HRow])
def head_to_head(team_id: int, opponent_id: int, limit: int = Query(20, ge=1, le=100),
                 session: Session = Depends(get_session)) -> list[H2HRow]:
    rows = session.execute(
        select(TeamMatch.date, TeamMatch.season, TeamMatch.is_home,
               TeamMatch.gf, TeamMatch.ga, TeamMatch.result)
        .where(TeamMatch.team_id == team_id, TeamMatch.opponent_id == opponent_id)
        .order_by(TeamMatch.date.desc())
        .limit(limit)
    ).all()
    return [H2HRow(date=d, season=s, is_home=h, gf=gf, ga=ga, result=r)
            for d, s, h, gf, ga, r in rows]
