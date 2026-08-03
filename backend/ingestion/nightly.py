"""Unattended nightly refresh (Phase 5, tier 1) — the part that CAN run headless.

Two idempotent, network-cheap steps against sources with no Cloudflare and no
rate limit, so they are safe to run unsupervised:

  1. Current-season league team data — football-data.co.uk CSV (ADR 0001).
  2. Points-deduction check — ESPN standings (ADR 0010); upserts new rulings.

PLAYER data is deliberately NOT here: FBref needs a headful, VPN-off,
Cloudflare-aware session that cannot run unattended — that is the *supervised*
tier (`ingestion.matchday`). Upcoming fixtures are their own scheduled task
(`ingestion.upcoming`, ADR 0009); this job does not duplicate them.

Registered in Windows Task Scheduler via ``backend/run_nightly.cmd`` (the same
pattern as the "BetStats upcoming fixtures" task). Idempotent — a catch-up run
after a missed night upserts, never duplicates.

Run:  python -m ingestion.nightly
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.facts import Fixture
from app.models.reference import Competition
from ingestion import points_adjustments, team_match
from ingestion.upcoming import season_for

# football-data.co.uk publishes a league's CSV only once that league has played,
# and then within roughly a day — so it lags kickoff. Wait this long after a
# league's FIRST fixture before treating a 404 as a real failure, otherwise the
# opening matchday would cry wolf every season.
PUBLISH_GRACE = dt.timedelta(hours=36)


def first_kickoffs(season: str, now: dt.datetime) -> dict[str, dt.datetime]:
    """Earliest fixture date per football-data.co.uk league key for ``season``.

    Read from the ESPN-sourced `fixtures` table (ADR 0009), which its own daily
    task keeps current — this is a DB read, never a fetch. Keyed to match the
    ``E1/2627`` form `team_match.ingest` reports skips under.
    """
    with SessionLocal() as session:
        rows = session.execute(
            select(Competition.fdcouk_key, func.min(Fixture.date))
            .join(Fixture, Fixture.competition_id == Competition.id)
            .where(Competition.fdcouk_key.is_not(None), Fixture.season == season)
            .group_by(Competition.fdcouk_key)
        ).all()
    return {f"{key}/{season}": kickoff for key, kickoff in rows}


def unexpected_skips(
    skipped: list[str], first_kickoff: dict[str, dt.datetime], now: dt.datetime
) -> list[str]:
    """The skips a not-yet-started season cannot explain — i.e. real failures.

    Pure, so the grace boundary is testable without a clock or a DB. A skip is
    forgiven while its league is pre-season; once that league has been playing
    for longer than `PUBLISH_GRACE`, missing data means the source is broken
    (URL change, outage) and must not pass as another quiet 404.
    """
    cutoff = now - PUBLISH_GRACE
    unexpected = []
    for skip in skipped:
        kickoff = first_kickoff.get(skip.split(":", 1)[0].strip())
        if kickoff is not None and kickoff < cutoff:
            unexpected.append(skip)
    return unexpected


def run_nightly(now: dt.datetime | None = None, log=print) -> dict:
    """Refresh the current season's league team data + points deductions.

    ``now`` defaults to the wall clock; it is injectable so the season boundary
    (``season_for``) is testable without freezing time.

    Raises ``RuntimeError`` if a league that has already kicked off returned no
    data — the alarm Task Scheduler turns into a blocking failure popup.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    season = season_for(now)
    log(f"[nightly] {now:%Y-%m-%d %H:%M} — refreshing current season {season}")

    team = team_match.ingest(seasons=[season])
    for key, n in team["per_league_season"].items():
        log(f"[nightly]   team {key}: {n} fixtures")
    for skip in team["skipped"]:
        log(f"[nightly]   SKIPPED {skip}")  # verdict below decides if it's fatal

    points = points_adjustments.ingest_points_adjustments(apply=True, log=log)
    log(f"[nightly] points adjustments in force: {len(points)}")

    # Last, so a dead source never costs us the points refresh above.
    broken = unexpected_skips(team["skipped"], first_kickoffs(season, now), now)
    if broken:
        raise RuntimeError(
            "football-data.co.uk returned nothing for league-season(s) already "
            "being played — source likely broken, not pre-season: "
            + "; ".join(broken)
        )

    return {"season": season, "team": team, "points": points}


if __name__ == "__main__":
    run_nightly()
