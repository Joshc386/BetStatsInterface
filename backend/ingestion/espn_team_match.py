"""ESPN-sourced league Team-Match rows (docs/adr/0015).

TIER 1, UNATTENDED. Plain JSON — no rate limiter, no Cloudflare, no VPN state,
no headful browser. One request per finished Fixture.

Why this exists: football-data.co.uk publishes league team data roughly a day
late and, for the 2026-27 Premier League, did not publish at all. The league
table is computed from `team_match` (ADR 0010), so a late source is a blank
table. FBref is not the answer — measured 2026-08-23 it is *slower* still
(end-of-day), covers only the top two tiers, and is Tier 2 supervised. ESPN had
complete stats within ten minutes of the final whistle, for all four tiers.

Team data therefore splits by RECENCY, not by provider: football-data.co.uk
remains the historical authority and RECLAIMS a Fixture when it finally
publishes; ESPN keeps the recent end current in the meantime. Player data is
untouched and stays entirely on FBref — ESPN carries no per-player tackles and
no minutes.

ESPN's card convention was verified to match football-data.co.uk's (a
two-booking dismissal is one red and no yellows), so nothing is normalised.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.facts import Fixture, TeamMatch
from app.models.reference import Competition, Team

# ESPN's stat name -> our Metric name. Only what `team_match` stores; ESPN also
# ships possession, passing and tackle figures we deliberately ignore.
_STAT_MAP = {
    "totalShots": "shots",
    "shotsOnTarget": "sot",
    "foulsCommitted": "fouls",
    "wonCorners": "corners",
    "yellowCards": "yellows",
    "redCards": "reds",
}


class EspnStatsUnavailable(Exception):
    """ESPN holds no usable stats for this Fixture.

    Not an error to alarm on — it is a normal, mostly historical, gap (4 of 25
    audited fixtures). The caller skips the Fixture and leaves it for
    football-data.co.uk, which is the authority for the historical record
    anyway.
    """


@dataclass(frozen=True)
class TeamStatLine:
    """One side of a Fixture, already paired with its opponent.

    `shots_conceded` / `sot_conceded` are the opposite side's attacking figures
    — ESPN has no such stat, and one summary covers both teams anyway.
    """

    espn_id: str
    name: str
    goals: int
    shots: int | None
    sot: int | None
    fouls: int | None
    corners: int | None
    yellows: int | None
    reds: int | None
    shots_conceded: int | None
    sot_conceded: int | None


def _int(value) -> int | None:
    """ESPN ships stats as display strings ('21', '0.3'). None stays None."""
    if value is None:
        return None
    return int(float(value))


def parse_summary(payload: dict) -> tuple[TeamStatLine, TeamStatLine]:
    """One match summary -> (home, away) stat lines.

    Goals come from the header's competitors, not the stat block — ESPN does
    not carry a goals stat, and the header score is the authoritative result
    (own goals included).
    """
    competition = payload["header"]["competitions"][0]
    goals: dict[str, int] = {}
    order: dict[str, str] = {}
    for competitor in competition["competitors"]:
        espn_id = str(competitor["team"]["id"])
        goals[espn_id] = int(competitor["score"])
        order[competitor["homeAway"]] = espn_id

    teams = payload.get("boxscore", {}).get("teams", [])
    if len(teams) != 2:
        raise EspnStatsUnavailable(f"boxscore holds {len(teams)} teams, expected 2")

    stats: dict[str, dict] = {}
    names: dict[str, str] = {}
    for team in teams:
        espn_id = str(team["team"]["id"])
        names[espn_id] = team["team"]["displayName"]
        raw = {s["name"]: s.get("displayValue") for s in team.get("statistics", [])}
        stats[espn_id] = {
            metric: _int(raw.get(espn_name))
            for espn_name, metric in _STAT_MAP.items()
        }

    # A zero-filled block is ESPN's way of saying "no data" for some older
    # fixtures — it ships the stat names with every value 0 rather than
    # omitting them. No real match has zero shots and zero fouls.
    if all(
        (s["shots"] or 0) == 0 and (s["fouls"] or 0) == 0 for s in stats.values()
    ):
        raise EspnStatsUnavailable("stat block is zero-filled (no real stats)")

    home_id, away_id = order["home"], order["away"]

    def line(espn_id: str, other_id: str) -> TeamStatLine:
        own, opp = stats[espn_id], stats[other_id]
        return TeamStatLine(
            espn_id=espn_id,
            name=names[espn_id],
            goals=goals[espn_id],
            shots_conceded=opp["shots"],
            sot_conceded=opp["sot"],
            **own,
        )

    return line(home_id, away_id), line(away_id, home_id)


class EspnFixtureMismatch(Exception):
    """ESPN's two sides are not this Fixture's two sides.

    Fail loud rather than write. Attaching one Fixture's figures to another is
    exactly the corruption found in football-data.co.uk on 2026-08-23 (Hull v
    Preston carrying Watford v Millwall's numbers verbatim), and it is invisible
    afterwards — the row looks perfectly ordinary.
    """


_METRICS = (
    "gf", "ga", "shots", "sot", "shots_conceded", "sot_conceded",
    "fouls", "corners", "yellows", "reds",
)


def write_team_rows(
    session: Session,
    fixture: Fixture,
    home: TeamStatLine,
    away: TeamStatLine,
) -> str:
    """Upsert this Fixture's two Team-Match rows from ESPN. Does not commit.

    Returns 'written' | 'skipped_fdcouk'.

    football-data.co.uk is the historical authority (ADR 0001) and reclaims a
    Fixture when it publishes, so ESPN never overwrites its rows — without that
    guard the two writers would overwrite each other on every run, forever.
    """
    existing = {
        row.team_id: row.source
        for row in session.scalars(
            select(TeamMatch).where(TeamMatch.fixture_id == fixture.id)
        )
    }
    if any(source != "espn" for source in existing.values()):
        return "skipped_fdcouk"

    sides = {
        str(session.get(Team, fixture.home_team_id).espn_id): fixture.home_team_id,
        str(session.get(Team, fixture.away_team_id).espn_id): fixture.away_team_id,
    }
    if {home.espn_id, away.espn_id} != set(sides):
        raise EspnFixtureMismatch(
            f"fixture {fixture.id} is {sorted(sides)}, "
            f"ESPN gave {sorted({home.espn_id, away.espn_id})}"
        )

    for line, other, is_home in ((home, away, True), (away, home, False)):
        vals = {
            "fixture_id": fixture.id,
            "competition_id": fixture.competition_id,
            "competition_type": "club_league",
            "season": fixture.season,
            "date": fixture.date,
            "team_id": sides[line.espn_id],
            "opponent_id": sides[other.espn_id],
            "is_home": is_home,
            "source": "espn",
            "gf": line.goals,
            "ga": other.goals,
            "shots": line.shots,
            "sot": line.sot,
            "shots_conceded": line.shots_conceded,
            "sot_conceded": line.sot_conceded,
            "fouls": line.fouls,
            "corners": line.corners,
            "yellows": line.yellows,
            "reds": line.reds,
        }
        session.execute(
            insert(TeamMatch)
            .values(**vals)
            .on_conflict_do_update(
                constraint="uq_team_match",
                set_={k: vals[k] for k in (("date", "source") + _METRICS)},
            )
        )
    session.flush()
    return "written"


_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary?event={eid}"
)


def pending_fixtures(session: Session, season: str | None = None) -> list[Fixture]:
    """Played league Fixtures carrying an ESPN id and holding no Team-Match row.

    Zero-network, and the whole reason a run with no football is nearly free:
    an empty list means no requests at all.

    Deliberately NOT "fixtures without a football-data.co.uk row" — once ESPN
    has written, the Fixture is served and re-fetching it every slot would
    spend a request to rewrite identical numbers.
    """
    query = (
        select(Fixture)
        .join(Competition, Competition.id == Fixture.competition_id)
        .where(
            Fixture.status == "finished",
            Competition.type == "club_league",
            Fixture.espn_event_id.is_not(None),
            ~select(TeamMatch.id)
            .where(TeamMatch.fixture_id == Fixture.id)
            .exists(),
        )
    )
    if season is not None:
        query = query.where(Fixture.season == season)
    return list(session.scalars(query.order_by(Fixture.date)))


def ingest(season: str | None = None, log=print) -> dict:
    """Write ESPN Team-Match rows for every pending league Fixture.

    One request per Fixture, committed per Fixture so a mid-run failure keeps
    what it earned. Idempotent: the next run simply finds fewer pending.

    `EspnStatsUnavailable` is a skip, not a failure — ESPN holds no stats for
    some (mostly older) fixtures and football-data.co.uk owns the historical
    record anyway. `EspnFixtureMismatch` is NOT caught: it means identity is
    wrong, and writing one Fixture's figures onto another is the one failure
    that is invisible afterwards.
    """
    from ingestion.upcoming import ESPN_LEAGUES, espn_json

    report = {"written": 0, "skipped_fdcouk": 0, "no_stats": 0}
    with SessionLocal() as session:
        pending = pending_fixtures(session, season)
        log(f"[espn-team] {len(pending)} pending fixture(s)")
        for fixture in pending:
            competition = session.get(Competition, fixture.competition_id)
            slug = ESPN_LEAGUES.get(competition.name)
            if slug is None:
                continue
            payload = espn_json(
                _SUMMARY_URL.format(slug=slug, eid=fixture.espn_event_id)
            )
            try:
                home, away = parse_summary(payload)
            except EspnStatsUnavailable as exc:
                report["no_stats"] += 1
                log(f"  [{fixture.id}] no stats: {exc}")
                continue
            outcome = write_team_rows(session, fixture, home, away)
            report[outcome] += 1
            session.commit()
            log(
                f"  [{fixture.id}] {competition.name} "
                f"{home.name} {home.goals}-{away.goals} {away.name} -> {outcome}"
            )
    return report


if __name__ == "__main__":
    print(ingest())
