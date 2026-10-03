"""Match report query layer — one finished Fixture's full record.

CONTEXT.md "Match report": every total the player rows carry is summed from
both squads' FBref Player-Match rows — the Referee hub's source (ADR 0018) — so
the totals reconcile with the player table beneath them and with the Referee's
figure for the same Fixture. Only the score and corners come from the
Team-Match row: own goals are credited to no player, and corners are not
recorded per player. Computed at read time, never stored.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models.facts import Fixture, PlayerMatch, TeamMatch
from app.models.reference import Competition, Player, Referee, Team
from app.referees import CARDS

PLAYER_METRICS = (
    "goals",
    "assists",
    "shots",
    "sot",
    "tackles",
    "fouls_committed",
    "fouls_drawn",
    "yellows",
    "reds",
    "cards",
)


def _totals(players: list[dict]) -> dict:
    """Each Metric summed over a side's rows — None where ANY row lacks it: the
    page did not publish it, and a sum over the rest would read low (ADR 0016)."""
    return {
        m: None
        if not players or any(p[m] is None for p in players)
        else sum(p[m] for p in players)
        for m in PLAYER_METRICS
    }


def _player(row) -> dict:
    p = row._asdict()
    del p["is_home"]
    # FBref writes a two-yellow dismissal as 2Y + 1R (CONTEXT.md "Cards")
    p["second_yellow"] = bool(p["yellows"] and p["yellows"] >= 2 and p["reds"])
    return p


def match_report(session: Session, *, fixture_id: int) -> dict | None:
    """Both sides of one Fixture: score and corners from the team rows, every
    other total from the player rows. None unless the Fixture was played: a
    scheduled one has the Fixture view instead."""
    home, away = aliased(Team), aliased(Team)
    fixture = session.execute(
        select(
            Fixture.id.label("fixture_id"),
            Fixture.date,
            Fixture.season,
            Competition.name.label("competition"),
            Competition.type.label("competition_type"),
            Fixture.stage,
            Fixture.referee_id,
            Referee.name.label("referee"),
            Fixture.home_team_id,
            home.canonical_name.label("home_team"),
            Fixture.away_team_id,
            away.canonical_name.label("away_team"),
        )
        .join(Competition, Competition.id == Fixture.competition_id)
        .join(home, home.id == Fixture.home_team_id)
        .join(away, away.id == Fixture.away_team_id)
        .outerjoin(Referee, Referee.id == Fixture.referee_id)
        .where(Fixture.id == fixture_id, Fixture.status == "finished")
    ).one_or_none()
    if fixture is None:
        return None

    team_rows = {
        r.is_home: r
        for r in session.execute(
            select(TeamMatch.is_home, TeamMatch.gf, TeamMatch.corners).where(
                TeamMatch.fixture_id == fixture_id
            )
        )
    }
    player_rows = session.execute(
        select(
            PlayerMatch.is_home,
            PlayerMatch.player_id,
            Player.canonical_name.label("player"),
            PlayerMatch.minutes,
            *(getattr(PlayerMatch, m) for m in PLAYER_METRICS if m != "cards"),
            CARDS.label("cards"),
        )
        .join(Player, Player.id == PlayerMatch.player_id)
        .where(PlayerMatch.fixture_id == fixture_id)
        # no starter flag is stored; minutes separates a start from a cameo
        .order_by(PlayerMatch.minutes.desc(), Player.canonical_name)
    ).all()

    def side(is_home: bool) -> dict:
        team = team_rows.get(is_home)
        players = [_player(r) for r in player_rows if r.is_home == is_home]
        return {
            "team_id": fixture.home_team_id if is_home else fixture.away_team_id,
            "team": fixture.home_team if is_home else fixture.away_team,
            "score": team.gf if team else None,
            "corners": team.corners if team else None,
            "totals": _totals(players),
            "players": players,
        }

    meta = fixture._asdict()
    for k in ("home_team_id", "home_team", "away_team_id", "away_team"):
        del meta[k]
    return {**meta, "home": side(True), "away": side(False)}
