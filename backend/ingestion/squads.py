"""ESPN roster ingestion — Squad membership (ADR 0013).

TIER 1, UNATTENDED. ESPN is unauthenticated, rate-limit-free and Cloudflare-free,
so unlike the FBref player pipeline this needs no VPN, no headful browser and no
supervision. ~92 requests (the four English tiers), idempotent, catch-up-safe.

Membership only — never a stat. Every Metric still comes from FBref.

Why this exists: ADR 0006 derived membership from appearances (**Recent squad**)
and accepted that a sold player lingers until he debuts elsewhere *in covered
data*. For anyone moving outside that coverage, "elsewhere" never arrives, so
the ghosts never clear — Wolves still listed Diego Costa and Patrick Cutrone,
25 of 44 members stale.

The hard part is identity. ESPN and FBref spell players differently
(Guðjohnsen/Gudjohnsen, Dapo/Oladapo Afolayan), and this project never joins
across sources on display names — so a name is resolved ONCE via a deterministic
ladder and then `players.espn_id` is stamped, after which spelling stops
mattering. Same pattern as `teams.espn_id` (ADR 0009).

Run:  python -m ingestion.squads
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.facts import Fixture, PlayerMatch, Squad
from app.models.reference import Competition, Player, Team
from ingestion.names import normalise_player_name
from ingestion.upcoming import espn_json, season_for

_ROSTER_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams/{eid}/roster"
)

# Clubs that get a Squad. The four English tiers — every one of their clubs
# carries an espn_id from the fixture slate (ADR 0009). A non-league cup
# opponent or a foreign European club has none and keeps Recent squad.
ROSTER_LEAGUES = {
    "Premier League": "eng.1",
    "Championship": "eng.2",
    "League One": "eng.3",
    "League Two": "eng.4",
}

# ESPN display name -> our canonical name, for the residue the ladder below
# cannot reach (a nickname bears no relation to the registered name:
# "Gaizka Larrazabal" vs "Larra"). Deterministic, never fuzzy. Extend when a run
# logs an unmatched name that is really a player we already hold.
ESPN_PLAYER_ALIASES: dict[str, str] = {}


@dataclass(frozen=True)
class RosterEntry:
    """One athlete on a club's ESPN roster. Membership only — no stats."""

    espn_id: str
    name: str
    position: str | None = None


def surname_key(name: str) -> str:
    """The normalised last token of a name."""
    tokens = normalise_player_name(name).split()
    return tokens[-1] if tokens else ""


def _initial_surname_key(name: str) -> str:
    """First initial + surname, e.g. 'Thiago Silva' -> 't silva'."""
    tokens = normalise_player_name(name).split()
    if len(tokens) < 2:
        return ""
    return f"{tokens[0][:1]} {tokens[-1]}"


def first_names_agree(a: str, b: str) -> bool:
    """Do two names' first tokens plausibly belong to the same person?

    True when either is a substring of the other ("Dapo" within "Oladapo"), or
    when either name is a single token (a mononym: our "Brau" against ESPN's
    "Miguel Ángel Brau"). Deterministic containment, never edit distance.

    This exists because a unique surname is NOT enough on its own. Against real
    rosters the bare-surname rung matched ESPN's "Tom King" onto our Joshua King
    and "Alfie Cresswell" onto our Charlie Cresswell — different people, one
    unique surname each, and the stamp would have been permanent and silent.
    """
    at, bt = normalise_player_name(a).split(), normalise_player_name(b).split()
    if not at or not bt:
        return False
    if len(at) == 1 or len(bt) == 1:
        return True
    x, y = at[0], bt[0]
    return x in y or y in x


def _only(matches: list[Player]) -> Player | None:
    """The single match, or None when a rung is ambiguous.

    Ambiguity must never be resolved by guessing: two Silvas at one club are two
    players, and merging them would silently attribute one's form to the other.
    """
    return matches[0] if len(matches) == 1 else None


def global_name_index(session: Session) -> dict[str, list[Player]]:
    """Every stored player keyed by normalised name.

    Built once per run and passed down, so the global rung costs one query
    rather than one per roster entry.
    """
    index: dict[str, list[Player]] = {}
    for player in session.scalars(select(Player)):
        index.setdefault(normalise_player_name(player.canonical_name), []).append(player)
    return index


def match_player(
    session: Session,
    candidates: list[Player],
    entry: RosterEntry,
    global_index: dict[str, list[Player]] | None = None,
) -> Player | None:
    """Resolve one roster entry to a stored player, or None.

    A deterministic ladder, loosest rung last, each step exact — no scoring, no
    edit distance. ``candidates`` is the club's own player set, which is what
    makes the surname rungs safe enough to attempt at all.

    ``global_index`` widens ONE rung beyond the club, for a transfer between
    tracked clubs: such a player is on his new club's roster but has appearances
    only at the old one, so every club-scoped rung is blind to him, and he is
    absent from the old club's roster too so he never gets stamped either. That
    rung is exact-full-name and unique-only — never a surname.
    """
    # 1. the stamped id — spelling-independent, and the reason transfers between
    #    tracked clubs resolve on the first run at the new club.
    stamped = session.scalars(
        select(Player).where(Player.espn_id == entry.espn_id)
    ).first()
    if stamped is not None:
        return stamped

    # 2. an explicit alias, resolved against this club's players
    target = ESPN_PLAYER_ALIASES.get(entry.name)
    if target is not None:
        key = normalise_player_name(target)
        hit = _only([p for p in candidates if normalise_player_name(p.canonical_name) == key])
        if hit is not None:
            return hit

    # 3. the full name
    key = normalise_player_name(entry.name)
    hit = _only([p for p in candidates if normalise_player_name(p.canonical_name) == key])
    if hit is not None:
        return hit

    # 3b. exact full name ANYWHERE we hold — the transfer-in case. Exact and
    #     unique only: a surname rung at global scope would be reckless.
    if global_index is not None:
        hit = _only(global_index.get(key, []))
        if hit is not None:
            return hit

    # 4. first initial + surname — decisive where a surname is shared
    key = _initial_surname_key(entry.name)
    if key:
        hit = _only(
            [p for p in candidates if _initial_surname_key(p.canonical_name) == key]
        )
        if hit is not None:
            return hit

    # 5. bare surname — the loosest rung, so it carries two guards: the surname
    #    must be unique in this club's set (_only), AND the first names must
    #    overlap. Uniqueness alone let "Tom King" become Joshua King.
    key = surname_key(entry.name)
    if key:
        hit = _only(
            [
                p
                for p in candidates
                if surname_key(p.canonical_name) == key
                and first_names_agree(p.canonical_name, entry.name)
            ]
        )
        if hit is not None:
            return hit

    return None


def parse_roster(payload: dict) -> list[RosterEntry]:
    """Flatten ESPN's roster payload, which may or may not group by position."""
    out: list[RosterEntry] = []
    for entry in payload.get("athletes", []):
        items = (
            entry["items"]
            if isinstance(entry, dict) and "items" in entry
            else [entry]
        )
        for a in items:
            name = a.get("fullName") or a.get("displayName")
            if not name:
                continue
            out.append(
                RosterEntry(
                    espn_id=str(a["id"]),
                    name=name,
                    position=(a.get("position") or {}).get("abbreviation"),
                )
            )
    return out


def fetch_roster(slug: str, espn_id: str) -> list[RosterEntry]:
    """One club's current roster. Raises on HTTP failure."""
    return parse_roster(espn_json(_ROSTER_URL.format(slug=slug, eid=espn_id)))


def club_candidates(session: Session, team_id: int) -> list[Player]:
    """Every player who has ever appeared for this club.

    Deliberately club-scoped: a global surname match would be reckless at this
    scale. A genuine transfer in from another tracked club still resolves, via
    the stamped espn_id rung.
    """
    return list(
        session.scalars(
            select(Player)
            .join(PlayerMatch, PlayerMatch.player_id == Player.id)
            .where(PlayerMatch.team_id == team_id)
            .distinct()
        )
    )


def refresh_squad(
    session: Session,
    team: Team,
    *,
    candidates: list[Player],
    fetch,
    global_index: dict[str, list[Player]] | None = None,
    log=print,
) -> dict:
    """Refresh one club's Squad from its roster. Idempotent.

    ``fetch`` is injected so the ladder and the write path can be tested without
    the network. Players no longer on the roster are marked inactive rather than
    deleted — that is what makes a departure visible immediately while keeping
    the row's history.
    """
    roster = fetch()
    today = dt.date.today()
    matched: set[int] = set()
    unmatched: list[str] = []
    stamped = 0

    for entry in roster:
        player = match_player(session, candidates, entry, global_index)
        if player is None:
            unmatched.append(entry.name)
            continue
        if not player.espn_id:
            player.espn_id = entry.espn_id
            stamped += 1
        matched.add(player.id)
        session.execute(
            insert(Squad)
            .values(
                team_id=team.id,
                player_id=player.id,
                active=True,
                last_seen=today,
            )
            .on_conflict_do_update(
                index_elements=["team_id", "player_id"],
                set_={"active": True, "last_seen": today},
            )
        )

    # anyone previously in this Squad but absent from today's roster has left
    deactivated = 0
    for row in session.scalars(
        select(Squad).where(Squad.team_id == team.id, Squad.active.is_(True))
    ):
        if row.player_id not in matched:
            row.active = False
            deactivated += 1

    session.flush()
    if unmatched:
        log(
            f"  {team.canonical_name}: {len(unmatched)} roster name(s) unmatched "
            f"- add ESPN_PLAYER_ALIASES entries if these are players we hold:"
        )
        for name in unmatched:
            log(f"      {name}")
    return {
        "team": team.canonical_name,
        "roster": len(roster),
        "matched": len(matched),
        "stamped": stamped,
        "unmatched": unmatched,
        "deactivated": deactivated,
    }


def _rostered_teams(session: Session, season: str) -> list[tuple[Team, str]]:
    """Every club playing in one of the four English tiers this season, with the
    ESPN slug to ask for its roster. Sourced from fixtures, so a promoted or
    relegated club follows the season automatically."""
    out: list[tuple[Team, str]] = []
    seen: set[int] = set()
    for comp_name, slug in ROSTER_LEAGUES.items():
        comp = session.scalar(
            select(Competition).where(Competition.name == comp_name)
        )
        if comp is None:
            continue
        ids: set[int] = set()
        for side in (Fixture.home_team_id, Fixture.away_team_id):
            ids |= set(
                session.scalars(
                    select(side).where(
                        Fixture.competition_id == comp.id, Fixture.season == season
                    )
                )
            )
        for team in session.scalars(select(Team).where(Team.id.in_(ids))):
            if team.espn_id and team.id not in seen:
                seen.add(team.id)
                out.append((team, slug))
    return out


def refresh_all(season: str | None = None, log=print) -> dict:
    """Refresh every rostered club's Squad. One request per club."""
    season = season or season_for(dt.datetime.now(tz=dt.timezone.utc))
    totals = {"clubs": 0, "roster": 0, "matched": 0, "stamped": 0,
              "unmatched": 0, "deactivated": 0, "failed": 0}
    with SessionLocal() as session:
        teams = _rostered_teams(session, season)
        index = global_name_index(session)
        log(f"[squads] {season}: refreshing {len(teams)} clubs "
            f"({len(index)} known player names)")
        for team, slug in teams:
            try:
                report = refresh_squad(
                    session,
                    team,
                    candidates=club_candidates(session, team.id),
                    fetch=lambda t=team, s=slug: fetch_roster(s, t.espn_id),
                    global_index=index,
                    log=log,
                )
            except Exception as exc:  # one club's outage is not the run's failure
                session.rollback()
                totals["failed"] += 1
                log(f"  {team.canonical_name}: FAILED {type(exc).__name__}: {exc}")
                continue
            session.commit()
            totals["clubs"] += 1
            totals["roster"] += report["roster"]
            totals["matched"] += report["matched"]
            totals["stamped"] += report["stamped"]
            totals["unmatched"] += len(report["unmatched"])
            totals["deactivated"] += report["deactivated"]
    log(f"[squads] done - {totals}")
    return totals


if __name__ == "__main__":
    import sys

    result = refresh_all()
    # A club whose roster could not be fetched leaves its Squad stale, which the
    # panel would show without complaint - so the exit code has to say so.
    sys.exit(1 if result["failed"] else 0)
