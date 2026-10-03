"""Leaderboard query layer — one league's top players per Metric (ADR 0019).

CONTEXT.md "Club window" / "Leaderboard": a player's figures are his
Appearances inside his club's last WINDOW League games that have player data,
counted only for the club of his most recent League Appearance. Every list is
ranked per 90 over his minutes, with half the window's minutes as the minimum.
Computed at read time, never stored.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import exists, select, tuple_
from sqlalchemy.orm import Session, aliased

from app.models.facts import PlayerMatch, TeamMatch
from app.models.reference import Competition, Player, Team
from app.referees import CARDS

WINDOW = 10  # games per club window
TOP = 10  # players per list
METRICS = (
    "shots",
    "sot",
    "goals",
    "assists",
    "tackles",
    "fouls_committed",
    "fouls_drawn",
    "cards",
)


def _club_windows(session: Session, club_ids: set[int]) -> dict[int, dict]:
    """Each club's last WINDOW League games with player data, newest first.

    A game whose team row has arrived but whose player rows have not is
    skipped, never counted as a game everyone missed, and kept as `awaiting`
    so the page can name it (ADR 0019)."""
    opp = aliased(Team)
    rows = session.execute(
        select(
            TeamMatch.team_id,
            TeamMatch.fixture_id,
            TeamMatch.date,
            Competition.name.label("competition"),
            opp.canonical_name.label("opponent"),
            exists()
            .where(
                PlayerMatch.fixture_id == TeamMatch.fixture_id,
                PlayerMatch.team_id == TeamMatch.team_id,
            )
            .label("has_players"),
        )
        .join(Competition, Competition.id == TeamMatch.competition_id)
        .join(opp, opp.id == TeamMatch.opponent_id)
        .where(
            TeamMatch.team_id.in_(club_ids),
            TeamMatch.competition_type == "club_league",
        )
        .order_by(TeamMatch.team_id, TeamMatch.date.desc())
    ).all()
    windows: dict[int, dict] = defaultdict(lambda: {"games": [], "awaiting": []})
    for r in rows:
        w = windows[r.team_id]
        if len(w["games"]) == WINDOW:
            continue
        if r.has_players:
            w["games"].append(r)
        else:
            w["awaiting"].append(
                {"fixture_id": r.fixture_id, "date": r.date, "opponent": r.opponent}
            )
    return windows


def _latest_clubs(session: Session, player_ids: set[int]) -> dict[int, int]:
    """The club of each player's most recent League Appearance."""
    rows = session.execute(
        select(PlayerMatch.player_id, PlayerMatch.team_id)
        .where(
            PlayerMatch.player_id.in_(player_ids),
            PlayerMatch.competition_type == "club_league",
        )
        .distinct(PlayerMatch.player_id)
        .order_by(
            PlayerMatch.player_id,
            PlayerMatch.date.desc(),
            PlayerMatch.fixture_id.desc(),  # deterministic same-day tiebreak
        )
    ).all()
    return {r.player_id: r.team_id for r in rows}


def _ranked(appearances: list, metric: str, minimum: dict[int, int]) -> list[dict]:
    """Per player: the Metric per 90 over his Recorded Appearances (ADR 0016),
    kept when his minutes reach his club's minimum; best first, then more
    minutes, then name."""
    by_player: dict[int, list] = defaultdict(list)
    for a in appearances:
        if getattr(a, metric) is not None:
            by_player[a.player_id].append(a)
    rows = []
    for apps in by_player.values():
        minutes = sum(a.minutes for a in apps)
        first = apps[0]
        if minutes < minimum[first.team_id]:
            continue
        total = sum(getattr(a, metric) for a in apps)
        rows.append({
            "player_id": first.player_id,
            "player": first.player,
            "team_id": first.team_id,
            "per90": total / minutes * 90,
            "total": total,
            "minutes": minutes,
            "apps": len(apps),
            # "carded in X of Y" sits beside the Cards rate only
            "carded_apps": sum(1 for a in apps if a.cards) if metric == "cards" else None,
        })
    rows.sort(key=lambda r: (-r["per90"], -r["minutes"], r["player"]))
    for r in rows:
        r["per90"] = round(r["per90"], 2)
    return rows[:TOP]


def leaderboard(session: Session, *, competition_id: int) -> dict | None:
    """The league's board for its current season; None unless the competition
    is a league with games on record."""
    competition = session.get(Competition, competition_id)
    if competition is None or competition.type != "club_league":
        return None
    season = session.scalar(
        select(TeamMatch.season)
        .where(TeamMatch.competition_id == competition_id)
        .order_by(TeamMatch.date.desc())
        .limit(1)
    )
    if season is None:
        return None
    club_ids = set(
        session.scalars(
            select(TeamMatch.team_id).where(
                TeamMatch.competition_id == competition_id,
                TeamMatch.season == season,
            )
        )
    )
    windows = _club_windows(session, club_ids)
    pairs = [(g.fixture_id, t) for t, w in windows.items() for g in w["games"]]
    appearances = session.execute(
        select(
            PlayerMatch.player_id,
            Player.canonical_name.label("player"),
            PlayerMatch.team_id,
            PlayerMatch.minutes,
            *(getattr(PlayerMatch, m) for m in METRICS if m != "cards"),
            CARDS.label("cards"),
        )
        .join(Player, Player.id == PlayerMatch.player_id)
        .where(tuple_(PlayerMatch.fixture_id, PlayerMatch.team_id).in_(pairs))
    ).all() if pairs else []
    latest = _latest_clubs(session, {a.player_id for a in appearances})
    mine = [a for a in appearances if latest.get(a.player_id) == a.team_id]
    # half the window's minutes; a club with fewer games has a smaller window
    minimum = {t: len(w["games"]) * 90 // 2 for t, w in windows.items()}
    names = dict(
        session.execute(select(Team.id, Team.canonical_name).where(Team.id.in_(club_ids))).all()
    )
    clubs = [
        {
            "team_id": t,
            "team": names[t],
            "games": len(w["games"]),
            "min_minutes": minimum[t],
            # newest first; more than one = the window crosses divisions
            "competitions": list(dict.fromkeys(g.competition for g in w["games"])),
            "awaiting": w["awaiting"],
        }
        for t, w in sorted(windows.items(), key=lambda tw: names[tw[0]])
    ]
    return {
        "competition_id": competition_id,
        "competition": competition.name,
        "season": season,
        "window": WINDOW,
        "clubs": clubs,
        "categories": {m: _ranked(mine, m, minimum) for m in METRICS},
    }
