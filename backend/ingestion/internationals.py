"""International-competition player-match ingestion (ADR 0011).

Whole-competition — EVERY played match, no covered-tie filter (the one structural
break from the cup/European path in ``cups.py``, which this otherwise reuses
wholesale). FBref-sourced; the condensed international match page (only ``summary``
+ ``keepers`` stat types, but ``summary`` carries shots/SoT/tackles/fouls/cards
inline) flows through the existing ``ingest_match`` unchanged.

The stored ``season`` is derived from each MATCH DATE (club season, August
boundary), NOT the soccerdata fetch-edition code: a summer tournament belongs to
the season that just ended, and a Nations League edition can straddle two stored
seasons (ADR 0011: a campaign straddling the boundary is held partially).

Nations (the "teams") auto-create as ``Team`` rows by ``fbref_id``, exactly like
foreign clubs in Europe; spelling drift between the schedule and the player-df
trips the fail-loud resolver in ``ingest_match`` → add an ``FBREF_TEAM_ALIASES``
entry + re-run (expected at foreign-name volume, ADR 0011).

Qualifiers are ADR-0011-scoped but BLOCKED upstream (soccerdata 1.9.0's
``read_seasons`` finds no ``table#seasons`` on FBref qualifier history pages) —
deferred pending a parser shim (memory: cups-internationals-sourcing).

Live fetches: VPN OFF, headful, under the watchdog (``ingestion.run_backfill``);
one-shot fetches hang on Cloudflare.
"""

from __future__ import annotations

import datetime as dt
import sys

import pandas as pd
from sqlalchemy import select

from app.db import SessionLocal
from app.models.facts import Fixture
from app.models.reference import Competition
from ingestion.cups import (
    _already_ingested,
    backfill_cup_team_match,
    get_or_create_cup_fixture,
    pair_team_ids,
    resolve_or_create_fbref_team,
)
from ingestion.players import (
    _FBREF_CACHE,
    _schedule_date,
    ingest_match,
    parse_player_ids,
    parse_team_ids,
)

# competition name -> soccerdata league key. Tournament finals + the Nations
# League only; qualifiers are deferred (blocked upstream). Nations League uses a
# two-year edition code ("2223"), the tournaments a single year ("2022").
LEAGUE_IDS = {
    "World Cup": "INT-World Cup",
    "Euros": "INT-European Championship",
    "Copa America": "INT-Copa America",
    "AFCON": "INT-Africa Cup of Nations",
    "Asian Cup": "INT-Asian Cup",
    "Gold Cup": "INT-Gold Cup",
    "Nations League": "INT-Nations League",
}


def season_for_date(date: dt.datetime) -> str:
    """Club-season code for a match date, AUGUST boundary (Aug→Jul).

    Aug–Dec → that calendar year starts the season; Jan–Jul → the previous year
    did. Distinct from ``ingestion.upcoming.season_for`` (July boundary, tuned for
    club pre-season): the August boundary keeps a June/July international
    tournament whole inside the season that just ended rather than splitting it at
    the June/July seam (ADR 0011 season-tag decision, 2026-07-07).
    """
    start = date.year if date.month >= 8 else date.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def select_all_games(schedule_df: pd.DataFrame) -> list[dict]:
    """Every played match in an international schedule (NO covered-tie filter).

    Rows with no ``game_id`` (unplayed) are dropped. Returns lightweight dicts —
    the schedule carries names + game_id only, so each nation's id is recovered
    from the match page at ingest (as in the cup path). The stored ``season`` is
    derived per-game from the match date; ``stage`` (the schedule round) joins the
    fixture natural key so a round-robin's two orientations never collide.
    """
    games: list[dict] = []
    for _, row in schedule_df.reset_index().iterrows():
        game_id = row.get("game_id")
        if game_id is None or pd.isna(game_id):
            continue
        date = _schedule_date(row)
        round_ = row.get("round")
        games.append(
            {
                "game_id": str(game_id),
                "date": date,
                "season": season_for_date(date),
                # International team names ARE the nation (no adjacent country
                # code like European club schedules carry), so take them as-is.
                "home_name": str(row["home_team"]).strip(),
                "away_name": str(row["away_team"]).strip(),
                "stage": "" if round_ is None or pd.isna(round_) else str(round_),
            }
        )
    return games


def backfill_international_season(
    edition: str,
    *,
    competition_name: str,
    league: str,
    limit: int | None = None,
    log=print,
) -> dict:
    """Backfill player_match for one international competition-edition from FBref.

    ONE persistent headful session (Cloudflare solved once). Reads the schedule,
    takes ALL played games, then per game: fetches the summary page, recovers both
    nations' fbref_ids, resolves/creates them, creates the fixture (stored season
    from the match date), reuses ``ingest_match``. Commits per match (resumable).
    REQUIRES THE VPN OFF and should run under the watchdog.

    ``edition`` is the soccerdata fetch code (e.g. "2022" for the 2022 World Cup,
    "2223" for the 2022-23 Nations League) — NOT the stored season.
    """
    import soccerdata as sd

    available = sd.FBref.available_leagues()
    if league not in available:
        raise ValueError(
            f"league {league!r} not available to the FBref reader; run "
            f"ingestion.config_sync first. available: {available}"
        )

    with SessionLocal() as session:
        competition = session.scalar(
            select(Competition).where(Competition.name == competition_name)
        )
        if competition is None:
            raise ValueError(f"competition {competition_name!r} not seeded")

        fb = sd.FBref(leagues=league, seasons=[edition], headless=False)
        log(f"[{competition_name} {edition}] fetching schedule (solves Cloudflare once)…")
        schedule = fb.read_schedule()
        games = select_all_games(schedule)
        log(f"[{competition_name} {edition}] {len(games)} played matches in schedule")

        created_fixtures = 0
        created_teams = 0
        skipped: list[str] = []
        for game in games:
            game_id = game["game_id"]
            if _already_ingested(session, game_id):
                continue  # resume: skip games already done, no network fetch
            try:
                df = fb.read_player_match_stats(stat_type="summary", match_id=game_id)
                html = (_FBREF_CACHE / f"match_{game_id}.html").read_text(
                    encoding="utf-8"
                )
                caption_ids = parse_team_ids(html)
                home_id, away_id = pair_team_ids(
                    game["home_name"], game["away_name"], caption_ids
                )
                if not home_id or not away_id:
                    skipped.append(f"{game_id}: could not pair teams to ids")
                    continue
                home, h_new = resolve_or_create_fbref_team(
                    session, game["home_name"], home_id
                )
                away, a_new = resolve_or_create_fbref_team(
                    session, game["away_name"], away_id
                )
                created_teams += int(h_new) + int(a_new)
                if h_new:
                    log(f"  + created national team {home.canonical_name!r} ({home_id})")
                if a_new:
                    log(f"  + created national team {away.canonical_name!r} ({away_id})")
                fixture = get_or_create_cup_fixture(
                    session, competition, game["season"], home, away,
                    game["date"], game_id, stage=game["stage"],
                )
                n = ingest_match(
                    session, fixture, competition.type, df, parse_player_ids(html)
                )
                session.commit()
                created_fixtures += 1
                log(f"  [{game_id}] {game['season']} {game['stage']}: {n} players")
            except Exception as exc:  # surface + skip one game, keep the run alive
                session.rollback()
                skipped.append(f"{game_id}: {type(exc).__name__}: {exc}")
                log(f"  SKIP {game_id}: {type(exc).__name__}: {exc}")
            if limit is not None and created_fixtures >= limit:
                break

        return {
            "competition": competition_name,
            "edition": edition,
            "games": len(games),
            "fixtures": created_fixtures,
            "created_teams": created_teams,
            "skipped": skipped,
        }


def stored_seasons(session, competition: Competition) -> list[str]:
    """The distinct stored (date-derived) seasons among a competition's fixtures."""
    return sorted(
        set(
            session.scalars(
                select(Fixture.season).where(
                    Fixture.competition_id == competition.id
                )
            )
        )
    )


def backfill_international_team_match(competition_name: str, log=print) -> dict:
    """Build team_match rows for every stored season of an international
    competition — ZERO network, reusing ``cups.backfill_cup_team_match`` per season.

    An edition can span two stored seasons (date-derived), so this loops the
    distinct seasons present among the competition's fixtures. Idempotent.
    """
    with SessionLocal() as session:
        competition = session.scalar(
            select(Competition).where(Competition.name == competition_name)
        )
        if competition is None:
            raise ValueError(f"competition {competition_name!r} not seeded")
        seasons = stored_seasons(session, competition)

    totals: dict = {"competition": competition_name, "seasons": {}, "built": 0}
    for s in seasons:
        rep = backfill_cup_team_match(s, cup_name=competition_name, log=log)
        totals["seasons"][s] = rep["built"]
        totals["built"] += rep["built"]
    return totals


if __name__ == "__main__":
    # Two modes:
    #   player (default):  python -m ingestion.internationals <edition> <comp> [limit]  (VPN off, watchdog)
    #   team (zero-network): python -m ingestion.internationals team <comp>
    argv = sys.argv[1:]
    team_mode = bool(argv) and argv[0] == "team"
    if team_mode:
        argv = argv[1:]

    if team_mode:
        comp = argv[0] if argv else "World Cup"
        report = backfill_international_team_match(comp)
        print(
            f"\n{report['competition']}: team_rows_built={report['built']} "
            f"seasons={report['seasons']}"
        )
    else:
        edition = argv[0] if len(argv) > 0 else "2022"
        comp = argv[1] if len(argv) > 1 else "World Cup"
        if comp not in LEAGUE_IDS:
            raise SystemExit(f"unknown competition {comp!r}; choose from {list(LEAGUE_IDS)}")
        limit = int(argv[2]) if len(argv) > 2 else None
        report = backfill_international_season(
            edition, competition_name=comp, league=LEAGUE_IDS[comp], limit=limit
        )
        print(
            f"\n{report['competition']} {report['edition']}: games={report['games']} "
            f"fixtures={report['fixtures']} created_teams={report['created_teams']} "
            f"skipped={len(report['skipped'])}"
        )
        for s in report["skipped"][:20]:
            print("  skip:", s)
