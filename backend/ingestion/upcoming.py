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
from sqlalchemy.orm import Session, aliased

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
    # Domestic cups (ADR 0012). LAST on purpose: an unresolved name rolls back
    # its own competition and raises, so the one slate with a long tail of
    # unfamiliar non-league clubs must run after every league has committed.
    "FA Cup": "eng.fa",
    "EFL Cup": "eng.league_cup",
}

# ESPN's "this is over" statuses. A SET, not one value — the 2026-08-21 spike
# found STATUS_FULL_TIME, STATUS_FINAL_PEN and STATUS_FINAL_AET together in one
# EFL Cup window, and cups are precisely where 90 minutes is not the end.
FINISHED_STATUSES = frozenset(
    {"STATUS_FULL_TIME", "STATUS_FINAL", "STATUS_FINAL_AET", "STATUS_FINAL_PEN"}
)

# ESPN's "this did not happen (yet)" statuses. A match called off keeps its
# original past date and stays `scheduled` — which is CORRECT under ADR 0014,
# since `finished` means played — but is indistinguishable from "nothing marked
# it finished", the failure that ADR exists to remove. Reading these tells the
# two apart at no extra cost: the payload is already in hand.
POSTPONED_STATUSES = frozenset(
    {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED", "STATUS_ABANDONED"}
)

# How long a played match may sit un-marked before it reads as broken plumbing.
# One full daily cycle plus slack: a match played at 19:45 is marked by the very
# next 07:30 run, so anything still scheduled a day later was not merely missed.
STALLED_AFTER = dt.timedelta(hours=24)

# How far BACK a slate looks. A played match is in the past, and a forward-only
# window would step straight over it: a match played at 19:45 is already outside
# a window starting the next morning, so the daily run would never once see it
# finished. 30 days also lets detection catch up after a stretch with the machine
# off, at no extra cost — same single request.
#
# Was cup-only until ADR 0014. Leagues need it for the same reason: without it
# nothing but football-data.co.uk ever marked a league Fixture finished, so an
# unpublished CSV withdrew the Fixture from FBref's player pipeline as well.
LOOKBACK_DAYS = 30


def takes_finished(competition_type: str) -> bool:
    """Whether this competition's slate should read FINISHED events, not just
    scheduled ones (ADR 0014).

    True for club competitions: something has to mark a Fixture played, and for
    leagues that used to be football-data.co.uk's job alone — a coupling that
    silently stalled FBref ingestion whenever the CSV was late.

    False for internationals, and that exception is load-bearing. ADR 0011's
    placeholders are ephemeral: `purge_stale_international_placeholders` deletes
    only *scheduled* past rows, so a placeholder marked finished would never be
    purged and would linger as precisely the ghost that function exists to
    prevent. Their real finished row comes from the FBref ingest, under a
    stage-qualified key no feed row could match.
    """
    return competition_type != "international"

# European competitions are DETECTED but never written (ADR 0012). Deliberately
# separate from ESPN_LEAGUES: ESPN serves every tie including the
# foreign-vs-foreign ones a Covered tie excludes, and European fixtures are keyed
# by stage, which an ESPN row could never match. Writing them would mean alias
# work for ~250 foreign clubs and a crop of ghost rows. Reading them costs one
# request and cannot break anything.
#
# The MAIN competitions only — never the uefa.*_qual slugs. The FBref-sourced
# European data contains no qualifying stage whatsoever, so a qualifying tie
# would become pending work FBref can never satisfy: a daily alarm, forever.
EUROPEAN_ESPN_SLUGS = {
    "Champions League": "uefa.champions",
    "Europa League": "uefa.europa",
    "Conference League": "uefa.europa.conf",
}

# A tie is "the same tie" if a covered club played in this competition within a
# day of the ESPN date. Kick-offs cross midnight UTC and the two sources do not
# always agree on the calendar day, so an exact match would flag ties we hold.
_SAME_TIE_WINDOW = dt.timedelta(days=1)

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
    """One scoreboard event, home side first.

    ``finished`` is only ever True for competitions that opt in via
    ``include_finished`` (the domestic cups) — fd.co.uk still owns league
    results, and ESPN is never a stats source for any scope.
    """

    date: dt.datetime
    home_espn_id: str
    away_espn_id: str
    home_names: tuple[str, str]  # (displayName, shortDisplayName)
    away_names: tuple[str, str]
    finished: bool = False


def espn_json(url: str, *, timeout: int = 30) -> dict:
    """GET one ESPN JSON document. Raises on HTTP failure.

    Sends **no** ``User-Agent`` override, so urllib's own default goes out. A
    descriptive ``betstats-research/1.0`` used to be sent from here and from
    `points_adjustments`; on 2026-08-05 ESPN's Akamai edge began answering
    **403 Access Denied** to it and both nightly jobs died together for two
    mornings. The rule keys on the *shape*, not the name — ``research/1.0`` and
    ``myapp/1.0`` are refused too, and so is a spoofed Chrome string (no
    matching browser fingerprint behind it), while ``curl/8.5.0``,
    ``python-requests/…`` and the urllib default all pass. Do not re-add a
    custom ``token/version`` here.

    One implementation on purpose: the two callers broke simultaneously because
    each carried its own copy of that header.
    """
    with urllib.request.urlopen(
        urllib.request.Request(url), timeout=timeout
    ) as resp:
        return json.load(resp)


def fetch_scoreboard(slug: str, start: dt.date, end: dt.date) -> dict:
    """One league's scoreboard JSON for a date window. Raises on HTTP failure."""
    return espn_json(_SCOREBOARD_URL.format(slug=slug, start=start, end=end))


def scoreboard_window(
    today: dt.date, days: int, *, lookback: bool
) -> tuple[dt.date, dt.date]:
    """The date range to ask ESPN for.

    Any slate that reads finished events has to reach backwards — a played match
    is behind us, never ahead — so `lookback` tracks `takes_finished`. Only the
    international slate, which wants unplayed placeholders and nothing else,
    asks for the forward window alone.
    """
    start = today - dt.timedelta(days=LOOKBACK_DAYS) if lookback else today
    return start, today + dt.timedelta(days=days)


def _event_date(raw: str) -> dt.datetime:
    """ESPN event dates are minute-precision Zulu: '2026-08-21T19:00Z'."""
    return dt.datetime.strptime(raw, "%Y-%m-%dT%H:%MZ").replace(
        tzinfo=dt.timezone.utc
    )


def postponed_pairs(payload: dict) -> set[tuple[str, str]]:
    """(home_espn_id, away_espn_id) for every called-off event in the payload.

    Deliberately separate from `parse_scoreboard`, which returns matches we act
    on. These are matches we must NOT act on, gathered only so a postponement is
    not mistaken for the slate being broken.
    """
    out: set[tuple[str, str]] = set()
    for event in payload.get("events", []):
        if event["status"]["type"]["name"] not in POSTPONED_STATUSES:
            continue
        sides = {
            c["homeAway"]: c["team"] for c in event["competitions"][0]["competitors"]
        }
        out.add((str(sides["home"]["id"]), str(sides["away"]["id"])))
    return out


def stalled(
    scheduled_past: list[tuple[str, str, dt.datetime]],
    postponed: set[tuple[str, str]],
    *,
    now: dt.datetime,
) -> list[tuple[str, str, dt.datetime]]:
    """Fixtures whose kick-off is long past that nothing has marked played, and
    that ESPN has not just told us were called off.

    This is the backstop for the ADR 0014 failure itself. The coverage audit
    only sees FINISHED Fixtures, so it is structurally blind to a Fixture that
    was never marked played at all — which is precisely what happened. Pure, so
    the boundary is testable without a clock.
    """
    cutoff = now - STALLED_AFTER
    return [
        (home, away, date)
        for home, away, date in scheduled_past
        if date < cutoff and (home, away) not in postponed
    ]


def _is_placeholder(team: dict) -> bool:
    """An undecided knockout slot — ESPN models it as a pseudo-team named
    'Quarterfinal 2 Winner' / 'Semifinal 1 Loser' (with a real id). No nation
    or club ends in Winner/Loser, so the suffix is the discriminator."""
    return team["displayName"].endswith((" Winner", " Loser"))


def parse_scoreboard(
    payload: dict, *, include_finished: bool = False
) -> list[ScheduledEvent]:
    """Scheduled events, plus finished ones when ``include_finished``.

    Anything in play or postponed is never returned. Knockout events with an
    undecided side are dropped too: a Fixture needs two real teams, so a
    semi-final appears on the first run after the quarter-finals resolve it.

    ``include_finished`` exists for the domestic cups (ADR 0012). Nothing marks
    a cup tie played — fd.co.uk does not cover cups — so without it matchday's
    pending probe is structurally blind to every cup round. Leagues stay
    scheduled-only: their results belong to fd.co.uk.
    """
    out: list[ScheduledEvent] = []
    for event in payload.get("events", []):
        status = event["status"]["type"]["name"]
        finished = status in FINISHED_STATUSES
        if status != "STATUS_SCHEDULED" and not (include_finished and finished):
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
                finished=finished,
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


def upsert_event(
    session: Session,
    competition: Competition,
    home_id: int,
    away_id: int,
    date: dt.datetime,
    finished: bool = False,
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
                status="finished" if finished else "scheduled",
            )
        )
        session.flush()
        return "created"
    if fixture.status == "finished":
        return "skipped_finished"
    if finished:
        # Promote in place. Domestic cups key on stage='' — the same natural key
        # cups.get_or_create_cup_fixture uses — so this row is the one the FBref
        # ingest later stamps with its match id. One row, never a ghost.
        fixture.status = "finished"
        fixture.date = date
        session.flush()
        return "finished"
    fixture.date = date
    session.flush()
    return "updated"


def _resolve_or_none(session: Session, espn_id: str, names: tuple[str, str]):
    """resolve_espn_team, but None instead of raising.

    A cup slate is mostly clubs we do not track, so an unresolved name is the
    normal case there, not an error.
    """
    try:
        return resolve_espn_team(session, espn_id, names)
    except UnknownEspnTeamError:
        return None


def select_cup_events(
    session: Session, events: list[ScheduledEvent], season: str
) -> tuple[list[tuple[ScheduledEvent, Team, Team]], list[str]]:
    """Filter a cup slate to Covered ties, resolving both sides.

    ESPN serves every tie in the competition, including the non-league early
    rounds we do not cover. Resolution is therefore lenient: a tie with no
    covered club is dropped without complaint, so an unfamiliar club is only
    ever alias work when it is actually standing opposite a club we track.

    Returns ``(keep, unresolved)`` — `keep` as (event, home, away) triples, and
    `unresolved` describing covered ties whose opponent could not be resolved.
    """
    from ingestion.cups import covered_team_ids  # local: cups imports players

    covered = covered_team_ids(session, season)
    keep: list[tuple[ScheduledEvent, Team, Team]] = []
    unresolved: list[str] = []
    for event in events:
        home = _resolve_or_none(session, event.home_espn_id, event.home_names)
        away = _resolve_or_none(session, event.away_espn_id, event.away_names)
        home_covered = home is not None and home.id in covered
        away_covered = away is not None and away.id in covered
        if not (home_covered or away_covered):
            continue  # not our tie — never alias work
        if home is None or away is None:
            missing = event.home_names if home is None else event.away_names
            unresolved.append(
                f"{event.date:%Y-%m-%d} {event.home_names[0]} v "
                f"{event.away_names[0]}: {missing[0]!r} matched no team"
            )
            continue
        keep.append((event, home, away))
    return keep, unresolved


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


def stalled_fixtures(
    session: Session,
    competition: Competition,
    season: str,
    postponed: set[tuple[str, str]],
    *,
    now: dt.datetime,
) -> list[str]:
    """Human-readable lines for this competition's stalled fixtures (ADR 0014).

    Reads the Fixtures that are past their kick-off and still `scheduled`, and
    asks `stalled` which of those ESPN has not just excused as called off.
    """
    home, away = aliased(Team), aliased(Team)
    rows = session.execute(
        select(
            home.espn_id, away.espn_id, Fixture.date,
            home.canonical_name, away.canonical_name,
        )
        .join(home, home.id == Fixture.home_team_id)
        .join(away, away.id == Fixture.away_team_id)
        .where(
            Fixture.competition_id == competition.id,
            Fixture.season == season,
            Fixture.status == "scheduled",
            Fixture.date < now,
        )
    ).all()
    names = {(h, a): (hn, an) for h, a, _, hn, an in rows}
    late = stalled([(h, a, d) for h, a, d, _, _ in rows], postponed, now=now)
    return [
        f"{d:%Y-%m-%d} {names[(h, a)][0]} v {names[(h, a)][1]}"
        for h, a, d in sorted(late, key=lambda r: r[2])
    ]


def ingest_upcoming(days: int = 45, *, log=print) -> dict:
    """Fetch + upsert the forward window for every configured league.

    Fail-loud per league: unknown ESPN names roll the league back and raise
    with every unresolved name listed (one run surfaces all alias work).

    Cups (ADR 0012) behave differently in two ways, both because ESPN serves the
    WHOLE competition — including the non-league rounds we do not cover. They
    take finished ties as well as scheduled ones (nothing else marks a cup tie
    played), and an unresolved name is only reported when it stands opposite a
    covered club. Reporting does not roll the slate back: the cups run last, and
    losing a whole cup slate over one non-league spelling would take the fixture
    view down with it. The run still ends non-zero, so the alias work alarms.
    """
    today = dt.date.today()
    now = dt.datetime.now(tz=dt.timezone.utc)
    season = season_for(now)
    report: dict[str, dict] = {}
    unresolved_cups: dict[str, list[str]] = {}
    stalled_all: dict[str, list[str]] = {}
    for comp_name, slug in ESPN_LEAGUES.items():
        with SessionLocal() as session:
            competition = session.scalars(
                select(Competition).where(Competition.name == comp_name)
            ).one()
            is_cup = competition.type == "club_cup"
            finished_too = takes_finished(competition.type)
            start, window_end = scoreboard_window(today, days, lookback=finished_too)
            payload = fetch_scoreboard(slug, start, window_end)
            events = parse_scoreboard(payload, include_finished=finished_too)
            unknown: list[str] = []
            counts = {
                "created": 0, "updated": 0, "finished": 0,
                "skipped_finished": 0, "purged": 0,
            }
            if competition.type == "international":
                counts["purged"] = purge_stale_international_placeholders(
                    session, competition, dt.datetime.now(tz=dt.timezone.utc)
                )
            if is_cup:
                keep, unknown = select_cup_events(session, events, season)
                for ev, home, away in keep:
                    counts[
                        upsert_event(
                            session, competition, home.id, away.id, ev.date,
                            finished=ev.finished,
                        )
                    ] += 1
            else:
                for ev in events:
                    try:
                        home = resolve_espn_team(session, ev.home_espn_id, ev.home_names)
                        away = resolve_espn_team(session, ev.away_espn_id, ev.away_names)
                    except UnknownEspnTeamError as exc:
                        unknown.append(str(exc))
                        continue
                    counts[
                        upsert_event(
                            session, competition, home.id, away.id, ev.date,
                            finished=ev.finished,
                        )
                    ] += 1
            if unknown and not is_cup:
                session.rollback()
                raise UnknownEspnTeamError(
                    f"{comp_name}: {len(unknown)} unresolved ESPN teams — add "
                    f"ESPN_TEAM_ALIASES entries:\n  " + "\n  ".join(unknown)
                )
            session.commit()
            if unknown:
                unresolved_cups[comp_name] = unknown
                log(
                    f"  {comp_name}: {len(unknown)} covered tie(s) with an "
                    f"unresolved opponent - add ESPN_TEAM_ALIASES entries:"
                )
                for line in unknown:
                    log(f"    {line}")
            # Backstop (ADR 0014): the coverage audit only sees FINISHED
            # Fixtures, so it is blind to one nothing ever marked played — which
            # is the failure that ADR exists to remove. Skipped for
            # internationals, whose past scheduled rows are placeholders the
            # purge above deliberately deletes rather than marks.
            if finished_too:
                late = stalled_fixtures(
                    session, competition, season, postponed_pairs(payload), now=now
                )
                if late:
                    stalled_all[comp_name] = late
                    log(
                        f"  {comp_name}: {len(late)} fixture(s) played but never "
                        f"marked finished - the slate is not updating:"
                    )
                    for line in late:
                        log(f"    {line}")
            report[comp_name] = {"events": len(events), **counts}
            log(f"  {comp_name}: {len(events)} events -> {counts}")
    if unresolved_cups:
        report["_unresolved_cups"] = unresolved_cups
    if stalled_all:
        report["_stalled"] = stalled_all
    return report


def european_pending_events(
    session: Session,
    competition: Competition,
    events: list[ScheduledEvent],
    season: str,
) -> list[ScheduledEvent]:
    """Played European ties involving a covered club that we hold no fixture for.

    The whole European detection path, and it writes nothing. Only the covered
    side is resolved — an unfamiliar foreign club is never looked up, so it can
    never become alias work and can never block the run. A tie with no covered
    club is not ours and is dropped in silence.
    """
    from ingestion.cups import covered_team_ids  # local: cups imports players

    covered = covered_team_ids(session, season)
    pending: list[ScheduledEvent] = []
    for event in events:
        if not event.finished:
            continue
        sides = [
            _resolve_or_none(session, event.home_espn_id, event.home_names),
            _resolve_or_none(session, event.away_espn_id, event.away_names),
        ]
        club = next((t for t in sides if t is not None and t.id in covered), None)
        if club is None:
            continue
        held = session.scalars(
            select(Fixture).where(
                Fixture.competition_id == competition.id,
                Fixture.season == season,
                Fixture.date >= event.date - _SAME_TIE_WINDOW,
                Fixture.date <= event.date + _SAME_TIE_WINDOW,
                (Fixture.home_team_id == club.id) | (Fixture.away_team_id == club.id),
            )
        ).first()
        if held is None:
            pending.append(event)
    return pending


def european_pending(season: str, *, days: int = LOOKBACK_DAYS, log=print) -> dict:
    """Per European competition, how many played covered ties we have not ingested.

    One ESPN request per competition, no Cloudflare, nothing written. A failure
    here is logged and treated as "no signal" rather than raised: the European
    round is not worth taking the match-day run down for, and the next run
    re-reads the same window anyway.
    """
    today = dt.date.today()
    start = today - dt.timedelta(days=days)
    pending: dict[str, int] = {}
    for comp_name, slug in EUROPEAN_ESPN_SLUGS.items():
        with SessionLocal() as session:
            competition = session.scalars(
                select(Competition).where(Competition.name == comp_name)
            ).one()
            try:
                events = parse_scoreboard(
                    fetch_scoreboard(slug, start, today), include_finished=True
                )
            except Exception as exc:  # ESPN 403/outage — no signal, not a failure
                log(f"  [european] {comp_name}: ESPN unavailable ({exc}) - skipping")
                continue
            found = european_pending_events(session, competition, events, season)
            if found:
                pending[comp_name] = len(found)
            session.rollback()  # first-contact espn_id stamping is not ours to keep
    return pending


if __name__ == "__main__":
    import sys

    window = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    result = ingest_upcoming(window)
    # A cup slate commits what it could resolve rather than rolling back, so the
    # exit code is the only thing that turns leftover alias work into an alarm.
    # Leftover alias work, or a slate that has stopped marking played matches
    # finished — the latter silently stalls FBref ingestion downstream, so it
    # must alarm here rather than be discovered weeks later (ADR 0014).
    sys.exit(1 if result.get("_unresolved_cups") or result.get("_stalled") else 0)
