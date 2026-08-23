"""Build the canonical `teams` universe from football-data.co.uk and provide the
get-or-create resolver used by team-data ingestion.

In v1 a team's canonical_name is its (cleaned) football-data.co.uk name; the same
string is stored as fdcouk_name. When FBref ingestion lands (Phase 4), FBref names
are mapped to these canonical rows via an explicit alias map, populating fbref_id.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.reference import Competition, Team
from ingestion.fdcouk import read_results
from ingestion.names import clean_name, guard_token, normalise_for_match

# Recent seasons define the current team universe. Historical backfill (Phase 3)
# upserts any older teams through the same resolver, so this set is a starting point.
UNIVERSE_SEASONS = ["2324", "2425", "2526"]


class UnknownFdcoukTeamError(RuntimeError):
    """A football-data.co.uk name that looks like a club we already hold."""


# football-data.co.uk name -> canonical name, where the CSV spells a club
# differently from the row we already hold. Deterministic, never fuzzy.
#
# The trigger (2026-08-23): fd.co.uk does not spell clubs the same way across
# divisions, and a RELEGATED club therefore arrives under a new name. Sheffield
# Wednesday went down to League One and the E2 CSV calls them "Sheffield Wed"
# where E1 said "Sheffield Weds"; Bradford are "Bradford City" in E2 and
# "Bradford" elsewhere. Both silently minted duplicate clubs.
FDCOUK_TEAM_ALIASES: dict[str, str] = {
    # 2026-27: relegated/promoted into a division that spells them differently
    "Sheffield Wed": "Sheffield Weds",
    "Bradford City": "Bradford",
}


def resolve_fdcouk_team(
    session: Session, fdcouk_name: str, *, allow_create: bool = False
) -> Team:
    """Return the canonical Team for a football-data.co.uk name.

    Idempotent: the same source name always resolves to the same row.

    ``allow_create`` distinguishes the two callers, and the distinction is the
    whole point. Building the universe (`build_team_universe`) legitimately
    creates every club it sees. Routine ingestion (`team_match`) must NOT: a
    name it has never seen mid-season is a RENAME, not a new club, and creating
    a row for it splits the club's history in silence — which is exactly what
    happened on 2026-08-23 (see FDCOUK_TEAM_ALIASES).

    Even with ``allow_create``, creation is refused when an existing club shares
    the name's first non-generic token — the signature of a respelling. Mirrors
    `cups.resolve_or_create_fbref_team`, which has guarded the FBref path this
    way since commit 1cbc322; this path simply never got it.
    """
    name = clean_name(fdcouk_name)
    name = FDCOUK_TEAM_ALIASES.get(name, name)
    team = session.scalar(select(Team).where(Team.fdcouk_name == name))
    if team is not None:
        return team

    token = guard_token(name)
    clash = next(
        (
            t
            for t in session.scalars(select(Team))
            if guard_token(t.fdcouk_name or t.canonical_name) == token
        ),
        None,
    )
    if clash is not None:
        raise UnknownFdcoukTeamError(
            f"football-data.co.uk name {name!r} is new, but {clash.canonical_name!r} "
            f"(id={clash.id}) already shares the token {token!r} — the signature of "
            f"one club spelled two ways. Same club -> add a FDCOUK_TEAM_ALIASES "
            f"entry; genuinely different -> seed the row deliberately."
        )
    if not allow_create:
        raise UnknownFdcoukTeamError(
            f"football-data.co.uk name {name!r} is not a club we hold. A new name "
            f"mid-season is a rename or a promoted club, never an auto-create: add "
            f"a FDCOUK_TEAM_ALIASES entry, or seed the club deliberately."
        )
    team = Team(canonical_name=name, fdcouk_name=name, country="England")
    session.add(team)
    session.flush()  # assign id
    return team


def find_duplicate_teams(session: Session) -> list[tuple[str, list[int]]]:
    """Surface canonical rows that collide on a normalised name (a silent split).

    Two distinct `teams` rows whose `canonical_name` or `fdcouk_name` normalise to
    the same key mean one real club has split into two — the exact failure the
    `fbref_id` identity spine (ADR 0007) exists to prevent. Read-only; returns
    ``[(normalised_key, [team_id, ...]), ...]`` for every colliding group, empty
    when the table is clean. Backs the standing regression guard in the tests.
    """
    by_key: dict[str, set[int]] = defaultdict(set)
    for team in session.scalars(select(Team)):
        for name in {team.canonical_name, team.fdcouk_name}:
            if name:
                by_key[normalise_for_match(name)].add(team.id)
    return [
        (key, sorted(ids)) for key, ids in sorted(by_key.items()) if len(ids) > 1
    ]


def build_team_universe(seasons: list[str] = UNIVERSE_SEASONS) -> dict:
    """Read football-data.co.uk for all configured league keys across `seasons`
    and upsert canonical teams. Returns a small report."""
    with SessionLocal() as session:
        fdcouk_keys = list(
            session.scalars(
                select(Competition.fdcouk_key).where(
                    Competition.fdcouk_key.is_not(None)
                )
            )
        )

        names: set[str] = set()
        skipped: list[str] = []
        for season in seasons:
            for key in fdcouk_keys:
                try:
                    df = read_results(season, key)
                    names |= set(df["HomeTeam"]) | set(df["AwayTeam"])
                except Exception as exc:  # network/parse — record, don't abort
                    skipped.append(f"{season}/{key}: {type(exc).__name__}")

        cleaned = sorted({clean_name(n) for n in names if str(n).strip()})
        created = 0
        for name in cleaned:
            before = session.scalar(
                select(Team.id).where(Team.fdcouk_name == name)
            )
            resolve_fdcouk_team(session, name, allow_create=True)
            if before is None:
                created += 1
        session.commit()
        total_count = session.query(Team).count()

    return {
        "seasons": seasons,
        "source_names": len(cleaned),
        "created": created,
        "total_teams": total_count,
        "skipped": skipped,
    }


if __name__ == "__main__":
    report = build_team_universe()
    print(
        f"team universe built from football-data.co.uk {report['seasons']}:\n"
        f"  distinct source names: {report['source_names']}\n"
        f"  newly created:         {report['created']}\n"
        f"  total teams in DB:     {report['total_teams']}\n"
        f"  skipped fetches:       {report['skipped'] or 'none'}"
    )
