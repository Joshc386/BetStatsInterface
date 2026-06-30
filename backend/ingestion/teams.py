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
from ingestion.names import clean_name, normalise_for_match

# Recent seasons define the current team universe. Historical backfill (Phase 3)
# upserts any older teams through the same resolver, so this set is a starting point.
UNIVERSE_SEASONS = ["2324", "2425", "2526"]


def resolve_fdcouk_team(session: Session, fdcouk_name: str) -> Team:
    """Return the canonical Team for a football-data.co.uk name, creating it if new.

    Idempotent: the same source name always resolves to the same row.
    """
    name = clean_name(fdcouk_name)
    team = session.scalar(select(Team).where(Team.fdcouk_name == name))
    if team is None:
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
            resolve_fdcouk_team(session, name)
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
