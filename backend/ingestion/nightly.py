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

from ingestion import points_adjustments, team_match
from ingestion.upcoming import season_for


def run_nightly(now: dt.datetime | None = None, log=print) -> dict:
    """Refresh the current season's league team data + points deductions.

    ``now`` defaults to the wall clock; it is injectable so the season boundary
    (``season_for``) is testable without freezing time.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    season = season_for(now)
    log(f"[nightly] {now:%Y-%m-%d %H:%M} — refreshing current season {season}")

    team = team_match.ingest(seasons=[season])
    for key, n in team["per_league_season"].items():
        log(f"[nightly]   team {key}: {n} fixtures")
    for skip in team["skipped"]:
        log(f"[nightly]   SKIPPED {skip}")  # network/parse — visible, non-fatal

    points = points_adjustments.ingest_points_adjustments(apply=True, log=log)
    log(f"[nightly] points adjustments in force: {len(points)}")

    return {"season": season, "team": team, "points": points}


if __name__ == "__main__":
    run_nightly()
