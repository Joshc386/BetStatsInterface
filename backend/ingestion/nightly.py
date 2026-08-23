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
from ingestion import coverage, points_adjustments, team_match
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


def _audit_team_coverage(now: dt.datetime, log) -> list[coverage.Gap]:
    """This job owns football-data.co.uk, so it owns the alarm for its gaps
    (ADR 0014). Opens its own session — nothing above holds one."""
    with SessionLocal() as session:
        overdue, _known = coverage.audit(
            session, coverage.TEAM_FDCOUK, now=now, log=log
        )
    return overdue


def run_nightly(
    now: dt.datetime | None = None, log=print, audit=None
) -> dict:
    """Refresh the current season's league team data + points deductions.

    ``now`` defaults to the wall clock; it is injectable so the season boundary
    (``season_for``) is testable without freezing time.

    Raises ``RuntimeError`` if a league that has already kicked off returned no
    data — the alarm Task Scheduler turns into a blocking failure popup. A
    failed points-deduction fetch is *not* such an alarm: ``points`` comes back
    empty and the run stays green (see the DEGRADED path below).
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    season = season_for(now)
    # See matchday: resolved here so monkeypatching actually works.
    audit = audit or _audit_team_coverage
    log(f"[nightly] {now:%Y-%m-%d %H:%M} — refreshing current season {season}")

    team = team_match.ingest(seasons=[season])
    for key, n in team["per_league_season"].items():
        log(f"[nightly]   team {key}: {n} fixtures")
    for skip in team["skipped"]:
        log(f"[nightly]   SKIPPED {skip}")  # verdict below decides if it's fatal

    # Deductions are an *enrichment* of the computed table, not the league data
    # this job exists to refresh, so a dead ESPN must not abort the run. On
    # 2026-08-05 it did: a 403 propagated out of here and killed two nightlies,
    # and because it raised *before* the verdict below, it also suppressed the
    # football-data.co.uk alarm — a genuinely broken Facts source would have
    # been masked by a broken footnote source. The previous ruling stays in the
    # table either way; the upsert is idempotent and re-runs tomorrow.
    try:
        points = points_adjustments.ingest_points_adjustments(apply=True, log=log)
        log(f"[nightly] points adjustments in force: {len(points)}")
    except Exception as e:
        points = []
        log(f"[nightly]   DEGRADED points adjustments: {type(e).__name__}: {e} "
            f"— existing rulings kept, retrying next run")

    # Two different questions about the same source, both asked last so a dead
    # source never costs us the points refresh above:
    #
    #   unexpected_skips — did the FETCH return anything at all for a league
    #     that has kicked off? Catches a URL change or outage (the 2026-08 E0
    #     HTTP 300) even before any Fixture is marked finished.
    #   coverage.audit  — did the DATA arrive, per Fixture? Catches PARTIAL
    #     publication, which the fetch check is structurally blind to: when
    #     fd.co.uk published 12 of 23 Championship games there was no skip at
    #     all, so the other 11 were invisible (ADR 0014).
    #
    # Kept as a pair deliberately — neither subsumes the other.
    overdue = audit(now, log)
    broken = unexpected_skips(team["skipped"], first_kickoffs(season, now), now)

    problems = []
    if broken:
        problems.append(
            "football-data.co.uk returned nothing for league-season(s) already "
            "being played — source likely broken, not pre-season: "
            + "; ".join(broken)
        )
    if overdue:
        problems.append(
            f"football-data.co.uk has not published {len(overdue)} played "
            "fixture(s) past their grace period: "
            + "; ".join(
                f"{g.competition} {g.season} {g.date:%Y-%m-%d}" for g in overdue[:10]
            )
        )
    if problems:
        raise RuntimeError(" | ".join(problems))

    return {"season": season, "team": team, "points": points, "overdue": overdue}


if __name__ == "__main__":
    run_nightly()
