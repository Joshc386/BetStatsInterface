"""Coverage audit — which finished Fixtures are still missing expected data.

ADR 0014. `Fixture.status == "finished"` means the match was *played*; whether a
source has published its data is a separate question, answered here by a join
rather than stored. Nothing in this module writes.

Judged **per Fixture per source**. The league-level check this supersedes
(`nightly.unexpected_skips`) could only see "this league published nothing", so
when football-data.co.uk published 12 of 23 Championship games the other 11 were
invisible while the Premier League's total absence alarmed loudly.

Reported in two tiers, because the audit must not cry wolf:
  * **overdue** — past its source's grace period and recent enough to act on.
    This is the alarm.
  * **known gap** — long past. Reported as a standing coverage figure, never
    alarmed on, never silently dropped (~79 minor-nation international Fixtures
    FBref appears to publish no lineups for).

Each job audits the source it owns, so an alarm always names something its own
caller can act on: `nightly` -> football-data.co.uk, `matchday` -> FBref.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.facts import Fixture, PlayerMatch, TeamMatch
from app.models.reference import Competition
from ingestion.matchday import LEAGUE_PLAYER_COMPETITIONS

TEAM_FDCOUK = "fd.co.uk team rows"
PLAYER_FBREF = "FBref player rows"

# Per-source, because their publishing rhythms differ by an order of magnitude:
# FBref has a match page up within hours, football-data.co.uk has repeatedly
# taken more than a day (and its 36h season-opening lag is on record). One
# shared number would either nag about fd.co.uk or go blind to FBref.
#
# PROVISIONAL (ADR 0014): deliberately NOT inherited from nightly's 36h
# PUBLISH_GRACE, which measured whole-league lateness at a season boundary — a
# different thing. Retune from what the audit actually reports in its first
# weeks rather than defending a guess.
GRACE = {
    TEAM_FDCOUK: dt.timedelta(hours=48),
    PLAYER_FBREF: dt.timedelta(hours=24),
}

# Past this, a gap stops being actionable and becomes a coverage fact.
KNOWN_GAP_AFTER = dt.timedelta(days=14)


@dataclass(frozen=True)
class Gap:
    """One Fixture missing one source's data."""

    fixture_id: int
    competition: str
    season: str
    date: dt.datetime
    source: str


def expected_sources(
    competition_name: str, competition_type: str, fdcouk_key: str | None
) -> frozenset[str]:
    """Which sources owe data for a Fixture in this competition.

    Read from the same facts the ingesters use, never a second list — a
    duplicate would drift from the original and start reporting fiction.

    football-data.co.uk is league-only (ADR 0001) and only where a CSV key
    exists. FBref owes player rows for the leagues in matchday's player scope;
    League One / Two are deliberately outside it, so their 6,649 finished
    Fixtures with no player rows are correct, not gaps. For every other scope a
    Fixture row only exists because the tie was in scope when it was written
    (`select_cup_events` keeps Covered ties only; internationals are ingested
    whole-competition), so its presence is itself the coverage decision.
    """
    owed = set()
    if competition_type == "club_league":
        if fdcouk_key:
            owed.add(TEAM_FDCOUK)
        if competition_name in LEAGUE_PLAYER_COMPETITIONS:
            owed.add(PLAYER_FBREF)
    else:
        owed.add(PLAYER_FBREF)
    return frozenset(owed)


def classify(gap: Gap, *, now: dt.datetime) -> str:
    """'within_grace' | 'overdue' | 'known_gap'. Pure, so the boundaries are
    testable without a clock."""
    age = now - gap.date
    if age < GRACE[gap.source]:
        return "within_grace"
    if age < KNOWN_GAP_AFTER:
        return "overdue"
    return "known_gap"


def split_tiers(
    gaps: list[Gap], *, now: dt.datetime
) -> tuple[list[Gap], list[Gap]]:
    """(overdue, known_gaps). Anything still within grace is in neither — it is
    recent, not late."""
    overdue = [g for g in gaps if classify(g, now=now) == "overdue"]
    known = [g for g in gaps if classify(g, now=now) == "known_gap"]
    return overdue, known


def find_gaps(session: Session, source: str, *, season: str | None = None) -> list[Gap]:
    """Every finished Fixture that is owed `source` and has none of its rows.

    One query per source; the expectation filter is applied in Python so it
    stays the single readable statement of who owes what.
    """
    fact = TeamMatch if source == TEAM_FDCOUK else PlayerMatch
    q = (
        select(
            Fixture.id,
            Competition.name,
            Competition.type,
            Competition.fdcouk_key,
            Fixture.season,
            Fixture.date,
        )
        .join(Competition, Competition.id == Fixture.competition_id)
        .where(
            Fixture.status == "finished",
            ~select(fact.id)
            .where(fact.fixture_id == Fixture.id)
            .exists(),
        )
    )
    if season is not None:
        q = q.where(Fixture.season == season)
    return [
        Gap(
            fixture_id=fid,
            competition=name,
            season=fseason,
            date=date,
            source=source,
        )
        for fid, name, ctype, key, fseason, date in session.execute(q)
        if source in expected_sources(name, ctype, key)
    ]


def audit(
    session: Session, source: str, *, now: dt.datetime, log=print
) -> tuple[list[Gap], list[Gap]]:
    """Find, split and report one source's gaps. Returns (overdue, known_gaps);
    the caller decides whether overdue is fatal — it owns the source."""
    overdue, known = split_tiers(find_gaps(session, source), now=now)
    if known:
        by_comp: dict[str, int] = {}
        for gap in known:
            by_comp[gap.competition] = by_comp.get(gap.competition, 0) + 1
        detail = ", ".join(f"{c} {n}" for c, n in sorted(by_comp.items()))
        log(f"[coverage] {source}: {len(known)} known gap(s) — {detail}")
    if overdue:
        log(f"[coverage] {source}: {len(overdue)} OVERDUE:")
        for gap in sorted(overdue, key=lambda g: g.date):
            log(
                f"    {gap.date:%Y-%m-%d} {gap.competition} {gap.season} "
                f"fixture {gap.fixture_id}"
            )
    else:
        log(f"[coverage] {source}: nothing overdue")
    return overdue, known
