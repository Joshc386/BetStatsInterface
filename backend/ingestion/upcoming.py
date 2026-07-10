"""Upcoming-fixture ingestion from the ESPN scoreboard API (ADR 0009).

Fetches a rolling forward window of scheduled fixtures per league and upserts
them as ``status='scheduled'`` Fixture rows on the natural key. Re-running at
intervals is the design: kick-off reshuffles simply update the row, and a
fixture is never demoted once finished (results arrive via fd.co.uk/FBref and
flip it through the same natural key). Scheduled fixtures are display-only —
they carry no stats and are never a stats source.

Teams resolve espn_id-first; first contact matches by normalised name (+ the
deterministic alias map) and stamps ``teams.espn_id`` — fail-loud on anything
unresolved, per the ADR 0007 seam pattern.

Run:  python -m ingestion.upcoming [days]     (default 45-day window)
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.request
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.facts import Fixture
from app.models.reference import Competition, Team
from ingestion.names import normalise_for_match

# Operator-facing: competition name -> ESPN league slug. The scoreboard API is
# the same shape for cups (eng.fa, eng.league_cup) — add them once draws exist
# and the covered-tie filter question is settled (ADR 0009).
ESPN_LEAGUES = {
    "Premier League": "eng.1",
    "Championship": "eng.2",
    "League One": "eng.3",
    "League Two": "eng.4",
    # Internationals (ADR 0011): display-only placeholders with a different
    # lifecycle from league fixtures — see upsert_scheduled / purge_stale.
    "World Cup": "fifa.world",
}

# ESPN display/short name -> canonical team name, for first-contact matching
# where normalisation alone cannot bridge the spelling. Deterministic, never
# fuzzy. Extend when a run fails loud on a new name.
ESPN_TEAM_ALIASES: dict[str, str] = {
    # 2026-27 first-contact backfill (2026-07-03)
    "Norwich City": "Norwich",
    "Nottingham Forest": "Nott'm Forest",
    "Tottenham Hotspur": "Tottenham",
    "Oxford United": "Oxford",
    "Peterborough United": "Peterboro",
    "Sheffield Wednesday": "Sheffield Weds",
    # York promoted into League Two 2026-27 — row seeded deliberately
    # (promoted National League clubs are a summer-prep seed, never auto-created)
    "York City": "York",
}

_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
    "?dates={start:%Y%m%d}-{end:%Y%m%d}&limit=400"
)


class UnknownEspnTeamError(Exception):
    """An ESPN team matched no stored espn_id, canonical/fdcouk name, or alias."""


@dataclass(frozen=True)
class ScheduledEvent:
    """One STATUS_SCHEDULED scoreboard event, home side first."""

    date: dt.datetime
    home_espn_id: str
    away_espn_id: str
    home_names: tuple[str, str]  # (displayName, shortDisplayName)
    away_names: tuple[str, str]


def fetch_scoreboard(slug: str, start: dt.date, end: dt.date) -> dict:
    """One league's scoreboard JSON for a date window. Raises on HTTP failure."""
    url = _SCOREBOARD_URL.format(slug=slug, start=start, end=end)
    req = urllib.request.Request(url, headers={"User-Agent": "betstats-research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _event_date(raw: str) -> dt.datetime:
    """ESPN event dates are minute-precision Zulu: '2026-08-21T19:00Z'."""
    return dt.datetime.strptime(raw, "%Y-%m-%dT%H:%MZ").replace(
        tzinfo=dt.timezone.utc
    )


def _is_placeholder(team: dict) -> bool:
    """An undecided knockout slot — ESPN models it as a pseudo-team named
    'Quarterfinal 2 Winner' / 'Semifinal 1 Loser' (with a real id). No nation
    or club ends in Winner/Loser, so the suffix is the discriminator."""
    return team["displayName"].endswith((" Winner", " Loser"))


def parse_scoreboard(payload: dict) -> list[ScheduledEvent]:
    """Scheduled events only — anything in play, finished, or postponed is not
    an upcoming fixture and is left to the results pipelines. Knockout events
    with an undecided side are dropped too: a Fixture needs two real teams, so
    a semi-final appears on the first run after the quarter-finals resolve it."""
    out: list[ScheduledEvent] = []
    for event in payload.get("events", []):
        if event["status"]["type"]["name"] != "STATUS_SCHEDULED":
            continue
        competitors = event["competitions"][0]["competitors"]
        sides = {c["homeAway"]: c["team"] for c in competitors}
        home, away = sides["home"], sides["away"]
        if _is_placeholder(home) or _is_placeholder(away):
            continue
        out.append(
            ScheduledEvent(
                date=_event_date(event["date"]),
                home_espn_id=str(home["id"]),
                away_espn_id=str(away["id"]),
                home_names=(home["displayName"], home["shortDisplayName"]),
                away_names=(away["displayName"], away["shortDisplayName"]),
            )
        )
    return out


def season_for(date: dt.datetime) -> str:
    """English season code for a kick-off date: July onward starts the new
    season (e.g. 2026-08 -> '2627'; 2027-05 -> '2627')."""
    start_year = date.year if date.month >= 7 else date.year - 1
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def resolve_espn_team(
    session: Session, espn_id: str, names: tuple[str, str]
) -> Team:
    """Resolve an ESPN competitor to a canonical Team, espn_id-first.

    First contact (no stored id) matches either ESPN name against canonical /
    fdcouk names + the alias map via ``normalise_for_match``, then stamps
    ``espn_id`` so later runs never depend on spelling. Fail-loud otherwise —
    an unknown name is alias work, not a team to auto-create (league fixtures
    only involve the 92 clubs we already track).
    """
    team = session.scalars(select(Team).where(Team.espn_id == espn_id)).first()
    if team is not None:
        return team

    candidates = {normalise_for_match(n) for n in names}
    candidates |= {
        normalise_for_match(ESPN_TEAM_ALIASES[n])
        for n in names
        if n in ESPN_TEAM_ALIASES
    }
    for team in session.scalars(select(Team)):
        keys = {normalise_for_match(team.canonical_name)}
        if team.fdcouk_name:
            keys.add(normalise_for_match(team.fdcouk_name))
        if keys & candidates:
            team.espn_id = espn_id
            session.flush()
            return team
    raise UnknownEspnTeamError(f"espn team {espn_id} {names!r} matched no team")


def upsert_scheduled(
    session: Session,
    competition: Competition,
    home_id: int,
    away_id: int,
    date: dt.datetime,
) -> str:
    """Create/update one scheduled fixture on the natural key.

    Returns 'created' | 'updated' | 'skipped_finished'. A finished fixture is
    never touched — the feed only ever moves kick-offs of unplayed games.

    International fixtures diverge twice (ADR 0011): the season is the
    August-boundary one the FBref ingest will store the played match under
    (July-boundary `season_for` would split a summer tournament); and the
    lookup is scheduled-rows-only, because a knockout pairing can repeat a
    finished group meeting with the same orientation (the finished row carries
    a stage; the feed knows none) — the placeholder must still be created.
    Placeholders are ephemeral: the FBref ingest creates its own finished row
    and `purge_stale_international_placeholders` removes the leftovers.
    """
    is_intl = competition.type == "international"
    if is_intl:
        from ingestion.internationals import season_for_date

        season = season_for_date(date)
    else:
        season = season_for(date)

    query = select(Fixture).where(
        Fixture.competition_id == competition.id,
        Fixture.season == season,
        Fixture.home_team_id == home_id,
        Fixture.away_team_id == away_id,
    )
    if is_intl:
        query = query.where(Fixture.status == "scheduled")
    fixture = session.scalars(query).first()
    if fixture is None:
        session.add(
            Fixture(
                competition_id=competition.id,
                season=season,
                date=date,
                home_team_id=home_id,
                away_team_id=away_id,
                status="scheduled",
            )
        )
        session.flush()
        return "created"
    if fixture.status == "finished":
        return "skipped_finished"
    fixture.date = date
    session.flush()
    return "updated"


def purge_stale_international_placeholders(
    session: Session, competition: Competition, now: dt.datetime
) -> int:
    """Delete this international competition's scheduled fixtures whose
    kick-off has passed. Their real, finished rows come from the FBref ingest
    under a stage-qualified key the feed can never match, so without this the
    placeholders would linger as ghosts. League fixtures are untouched — their
    ingest reuses the scheduled row in place (same natural key), which is why
    only internationals need a purge."""
    stale = session.scalars(
        select(Fixture).where(
            Fixture.competition_id == competition.id,
            Fixture.status == "scheduled",
            Fixture.date < now,
        )
    ).all()
    for fixture in stale:
        session.delete(fixture)
    session.flush()
    return len(stale)


def ingest_upcoming(days: int = 45, *, log=print) -> dict:
    """Fetch + upsert the forward window for every configured league.

    Fail-loud per league: unknown ESPN names roll the league back and raise
    with every unresolved name listed (one run surfaces all alias work).
    """
    today = dt.date.today()
    end = today + dt.timedelta(days=days)
    report: dict[str, dict] = {}
    for comp_name, slug in ESPN_LEAGUES.items():
        with SessionLocal() as session:
            competition = session.scalars(
                select(Competition).where(Competition.name == comp_name)
            ).one()
            events = parse_scoreboard(fetch_scoreboard(slug, today, end))
            unknown: list[str] = []
            counts = {"created": 0, "updated": 0, "skipped_finished": 0, "purged": 0}
            if competition.type == "international":
                counts["purged"] = purge_stale_international_placeholders(
                    session, competition, dt.datetime.now(tz=dt.timezone.utc)
                )
            for ev in events:
                try:
                    home = resolve_espn_team(session, ev.home_espn_id, ev.home_names)
                    away = resolve_espn_team(session, ev.away_espn_id, ev.away_names)
                except UnknownEspnTeamError as exc:
                    unknown.append(str(exc))
                    continue
                counts[upsert_scheduled(session, competition, home.id, away.id, ev.date)] += 1
            if unknown:
                session.rollback()
                raise UnknownEspnTeamError(
                    f"{comp_name}: {len(unknown)} unresolved ESPN teams — add "
                    f"ESPN_TEAM_ALIASES entries:\n  " + "\n  ".join(unknown)
                )
            session.commit()
            report[comp_name] = {"events": len(events), **counts}
            log(f"  {comp_name}: {len(events)} scheduled -> {counts}")
    return report


if __name__ == "__main__":
    import sys

    window = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    ingest_upcoming(window)
