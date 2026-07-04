"""Points-deduction ingestion from the ESPN standings API (docs/adr/0010).

The computed league table (GET /table) derives everything from team_match
except administrative points changes. This job fetches ESPN's historical
standings per league-season, extracts the ``deductions`` stat (value = points
removed, displayValue = the ruling note) and upserts one negative-points row
per club-season into ``points_adjustments``. Idempotent; ~1 request per
league-season, one-off for history and re-runnable in-season for new rulings.

Teams resolve espn_id-first via the ADR 0007/0009 seam (`resolve_espn_team`),
fail-loud on anything unresolved.

Run:  python -m ingestion.points_adjustments          # dry-run: print, no writes
      python -m ingestion.points_adjustments --apply  # upsert + commit
"""

from __future__ import annotations

import json
import sys
import urllib.request

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.facts import PointsAdjustment, TeamMatch
from app.models.reference import Competition
from ingestion.upcoming import ESPN_LEAGUES, resolve_espn_team

_STANDINGS_URL = (
    "https://site.api.espn.com/apis/v2/sports/soccer/{slug}/standings"
    "?season={start_year}"
)


def espn_season_year(season: str) -> int:
    """Season code -> ESPN season param (the start year): '2324' -> 2023."""
    return 2000 + int(season[:2])


def fetch_standings(slug: str, season: str) -> dict:
    """One league-season's standings JSON. Raises on HTTP failure."""
    url = _STANDINGS_URL.format(slug=slug, start_year=espn_season_year(season))
    req = urllib.request.Request(url, headers={"User-Agent": "betstats-research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def parse_deductions(payload: dict) -> list[dict]:
    """Entries with a non-zero ``deductions`` stat.

    Returns ``[{espn_id, names, points, note}]`` with ``points`` already
    negative (ESPN's value counts points removed).
    """
    entries = payload["children"][0]["standings"]["entries"]
    out: list[dict] = []
    for entry in entries:
        stat = next(s for s in entry["stats"] if s["name"] == "deductions")
        if not stat["value"]:
            continue
        team = entry["team"]
        points = -int(stat["value"])
        # ESPN occasionally ships a junk note (West Brom 2526 is ".") — fall
        # back to a generic one rather than footnoting punctuation.
        note = stat["displayValue"].strip()
        if not note.strip("."):
            note = f"{-points}-point deduction (EFL/ESPN, no detail given)"
        out.append(
            {
                "espn_id": str(team["id"]),
                "names": (team["displayName"], team["shortDisplayName"]),
                "points": points,
                "note": note,
            }
        )
    return out


def league_seasons(session: Session, competition_id: int) -> list[str]:
    """Seasons with ingested team_match rows — the window worth seeding."""
    return sorted(
        session.scalars(
            select(TeamMatch.season)
            .where(TeamMatch.competition_id == competition_id)
            .distinct()
        )
    )


def ingest_points_adjustments(*, apply: bool = False, log=print) -> list[dict]:
    """Fetch every covered league-season, upsert deductions. Returns the rows.

    Dry-run (default) prints what would be written and rolls back — including
    any first-contact espn_id stamps.
    """
    found: list[dict] = []
    with SessionLocal() as session:
        for comp_name, slug in ESPN_LEAGUES.items():
            competition = session.scalars(
                select(Competition).where(Competition.name == comp_name)
            ).one()
            for season in league_seasons(session, competition.id):
                for d in parse_deductions(fetch_standings(slug, season)):
                    team = resolve_espn_team(session, d["espn_id"], d["names"])
                    session.execute(
                        insert(PointsAdjustment)
                        .values(
                            competition_id=competition.id,
                            season=season,
                            team_id=team.id,
                            points=d["points"],
                            note=d["note"],
                        )
                        .on_conflict_do_update(
                            constraint="uq_points_adjustment",
                            set_={"points": d["points"], "note": d["note"]},
                        )
                    )
                    found.append(
                        {
                            "competition": comp_name,
                            "season": season,
                            "team": team.canonical_name,
                            "points": d["points"],
                            "note": d["note"],
                        }
                    )
                    log(
                        f"  {comp_name} {season}: {team.canonical_name} "
                        f"{d['points']:+d} — {d['note']}"
                    )
        if apply:
            session.commit()
            log(f"COMMITTED {len(found)} adjustment(s)")
        else:
            session.rollback()
            log(f"DRY-RUN: {len(found)} adjustment(s) found, nothing written")
    return found


if __name__ == "__main__":
    ingest_points_adjustments(apply="--apply" in sys.argv)
