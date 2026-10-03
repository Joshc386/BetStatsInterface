"""Referee hub query layer (docs/adr/0018).

A Referee's Rolling Window iterates over the Fixtures he refereed; each Metric
is a MATCH TOTAL, both sides summed from FBref Player-Match rows (never from
team_match, whose league rows follow football-data.co.uk's card convention).
Computed at query time; nothing derived is stored.

Recorded, per ADR 0016: a Fixture counts toward a rate only where its page
published that Metric. Cards/yellows/reds need both sides' player rows; fouls
also need every row's `fouls_committed` (a page publishes it all-or-nothing).
"""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from app.models.facts import Fixture, PlayerMatch
from app.models.reference import Competition, Referee, Team

METRICS = ("cards", "yellows", "reds", "fouls")

SCOPE_LABELS = {
    "club_league": "League",
    "club_cup": "Cups",
    "club_european": "Europe",
    "international": "International",
    "all": "All competitions",
}

# CONTEXT.md "Cards": every yellow, plus every red that did NOT follow a second
# yellow — at most 2 per player. FBref writes a two-yellow dismissal as 2Y + 1R
# and never publishes 2CrdY, so a red on a player with 2 yellows IS that red.
_CARDS = (
    PlayerMatch.yellows
    + PlayerMatch.reds
    - case(((PlayerMatch.reds > 0) & (PlayerMatch.yellows >= 2), 1), else_=0)
)


def _window_fixtures(session, referee_id, scope, n, seasons):
    """His finished Fixtures in scope, newest first, cut to the window."""
    home, away = aliased(Team), aliased(Team)
    q = (
        select(
            Fixture.id.label("fixture_id"),
            Fixture.date,
            Fixture.season,
            Competition.name.label("competition"),
            Competition.type.label("competition_type"),
            Fixture.home_team_id.label("home_id"),
            home.canonical_name.label("home"),
            Fixture.away_team_id.label("away_id"),
            away.canonical_name.label("away"),
        )
        .join(Competition, Competition.id == Fixture.competition_id)
        .join(home, home.id == Fixture.home_team_id)
        .join(away, away.id == Fixture.away_team_id)
        .where(Fixture.referee_id == referee_id, Fixture.status == "finished")
        .order_by(Fixture.date.desc())
    )
    if scope != "all":
        q = q.where(Competition.type == scope)
    if seasons:
        q = q.where(Fixture.season.in_(seasons))
    else:
        q = q.limit(n)
    return list(session.execute(q).all())


def _side_totals(session, fixture_ids) -> dict[tuple[int, bool], dict]:
    """Per (fixture, is_home): Cards / yellows / reds / fouls from player rows.

    A Metric is None for a side where ANY row lacks it — the page did not
    publish it, and SUM would silently skip the blank row and read low. Fouls
    is sparse in practice; cards never are today, but get the same rule."""
    if not fixture_ids:
        return {}
    rows = session.execute(
        select(
            PlayerMatch.fixture_id,
            PlayerMatch.is_home,
            func.sum(_CARDS).label("cards"),
            func.sum(PlayerMatch.yellows).label("yellows"),
            func.sum(PlayerMatch.reds).label("reds"),
            func.sum(PlayerMatch.fouls_committed).label("fouls"),
            func.count().label("players"),
            func.count(PlayerMatch.yellows).label("yellow_rows"),
            func.count(PlayerMatch.reds).label("red_rows"),
            func.count(PlayerMatch.fouls_committed).label("fouls_rows"),
        )
        .where(PlayerMatch.fixture_id.in_(fixture_ids))
        .group_by(PlayerMatch.fixture_id, PlayerMatch.is_home)
    ).all()
    out = {}
    for r in rows:
        cards_ok = r.yellow_rows == r.players and r.red_rows == r.players
        out[(r.fixture_id, r.is_home)] = {
            "cards": int(r.cards) if cards_ok else None,
            "yellows": int(r.yellows) if r.yellow_rows == r.players else None,
            "reds": int(r.reds) if r.red_rows == r.players else None,
            "fouls": int(r.fouls) if r.fouls_rows == r.players else None,
        }
    return out


def _blank_side() -> dict:
    return dict.fromkeys(METRICS)


def _match_total(home: dict, away: dict, metric: str) -> int | None:
    """A match total exists only when BOTH sides recorded the Metric."""
    if home[metric] is None or away[metric] is None:
        return None
    return home[metric] + away[metric]


def _hit_rate(values: list[int], threshold, direction) -> dict | None:
    if threshold is None or not values:
        return None
    if direction == "under":
        hits = sum(1 for v in values if v <= threshold)
    else:
        hits = sum(1 for v in values if v >= threshold)
    return {
        "threshold": threshold,
        "direction": direction,
        "hits": hits,
        "n": len(values),
        "pct": round(100 * hits / len(values), 1),
    }


def referee_summary(
    session: Session,
    *,
    referee_id: int,
    metric: str = "cards",
    n: int = 10,
    seasons: list[str] | None = None,
    scope: str = "club_league",
    threshold: float | None = None,
    direction: str = "over",
) -> dict | None:
    """Headline rates, home/away split, hit-rate and per-match rows for one
    referee over one window. None when the referee does not exist."""
    name = session.scalar(select(Referee.name).where(Referee.id == referee_id))
    if name is None:
        return None

    fixtures = _window_fixtures(session, referee_id, scope, n, seasons)
    sides = _side_totals(session, [f.fixture_id for f in fixtures])

    games = []
    for f in reversed(fixtures):  # chronological, like every breakdown
        home = sides.get((f.fixture_id, True), _blank_side())
        away = sides.get((f.fixture_id, False), _blank_side())
        games.append({
            **f._asdict(),
            **{m: _match_total(home, away, m) for m in METRICS},
            "home_side": home,
            "away_side": away,
        })

    rates, home_rates, away_rates = {}, {}, {}
    for m in METRICS:
        recorded = [g for g in games if g[m] is not None]
        k = len(recorded)
        rates[m] = {
            "total": sum(g[m] for g in recorded),
            "recorded": k,
            "per_match": sum(g[m] for g in recorded) / k if k else None,
        }
        home_rates[m] = sum(g["home_side"][m] for g in recorded) / k if k else None
        away_rates[m] = sum(g["away_side"][m] for g in recorded) / k if k else None

    # Matches per scope across his whole record, so the page can show what each
    # scope choice holds — and default sensibly for a referee with no League.
    scope_counts = dict(
        session.execute(
            select(Competition.type, func.count())
            .join(Fixture, Fixture.competition_id == Competition.id)
            .where(Fixture.referee_id == referee_id, Fixture.status == "finished")
            .group_by(Competition.type)
        ).all()
    )
    domestic = scope_counts.get("club_league", 0) + scope_counts.get("club_cup", 0)

    return {
        "referee_id": referee_id,
        "name": name,
        "metric": metric,
        "scope": scope,
        "scope_label": SCOPE_LABELS[scope],
        "window": (f"seasons {', '.join(seasons)}" if seasons else f"last {n}")
        + f" · {SCOPE_LABELS[scope]}",
        "matches": len(games),
        "rates": rates,
        "home_rates": home_rates,
        "away_rates": away_rates,
        "hit_rate": _hit_rate(
            [g[metric] for g in games if g[metric] is not None], threshold, direction
        ),
        "scope_counts": scope_counts,
        # UEFA appoints neutral referees, so a referee we never see at home is
        # seen only in his ties involving covered clubs (CONTEXT.md "Referee").
        "covered_ties_only": domestic == 0 and scope_counts.get("club_european", 0) > 0,
        "games": games,
    }


def referee_list(session: Session) -> list[dict]:
    """Every referee with a recorded Fixture, most recently active first —
    the Appointed-referee picker's source, so this weekend's men lead."""
    rows = session.execute(
        select(
            Referee.id,
            Referee.name,
            func.count(Fixture.id).label("matches"),
            func.max(Fixture.date).label("last_date"),
        )
        .join(Fixture, Fixture.referee_id == Referee.id)
        .where(Fixture.status == "finished")
        .group_by(Referee.id, Referee.name)
        .order_by(func.max(Fixture.date).desc(), Referee.name)
    ).all()
    return [r._asdict() for r in rows]
