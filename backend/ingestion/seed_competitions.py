"""Seed the v1 competitions (top 4 English tiers + Championship play-offs).

Idempotent: upserts on the unique competition name. Run after migrations:
    python -m ingestion.seed_competitions
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models.reference import Competition

# name, type, tier, fdcouk_key, fbref_key
#   tier is the English-pyramid rank (league only); None for the knockout.
#   fdcouk_key is None where football-data.co.uk doesn't cover the competition.
#   fbref_key is None where the competition has no standalone FBref page — the
#   play-offs are sourced from the Championship schedule (round = "play-offs").
COMPETITIONS = [
    ("Premier League", "club_league", 1, "E0", "Premier League"),
    ("Championship", "club_league", 2, "E1", "Championship"),
    ("League One", "club_league", 3, "E2", None),
    ("League Two", "club_league", 4, "E3", None),
    # Promotion play-offs: 3rd-6th play two-legged semis + a Wembley final.
    # Domestic knockout (club_cup) so it never counts as league form; player
    # data only (football-data.co.uk has no play-off coverage).
    ("Championship Play-offs", "club_cup", None, None, None),
    # Domestic cups (ADR 0008): player-only, FBref-sourced (fbref_key set;
    # football-data.co.uk does not cover cups, so fdcouk_key=None). Distinct
    # competition_id keeps "FA Cup form" and "EFL Cup form" separable.
    ("FA Cup", "club_cup", None, None, "FA Cup"),
    ("EFL Cup", "club_cup", None, None, "EFL Cup"),
]


def seed_competitions() -> int:
    rows = [
        {
            "name": name,
            "type": ctype,
            "country": "England",
            "tier": tier,
            "fdcouk_key": fdcouk_key,
            "fbref_key": fbref_key,
        }
        for (name, ctype, tier, fdcouk_key, fbref_key) in COMPETITIONS
    ]
    stmt = insert(Competition).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_competitions_name",
        set_={
            "type": stmt.excluded.type,
            "country": stmt.excluded.country,
            "tier": stmt.excluded.tier,
            "fdcouk_key": stmt.excluded.fdcouk_key,
            "fbref_key": stmt.excluded.fbref_key,
        },
    )
    with SessionLocal() as session:
        session.execute(stmt)
        session.commit()
    return len(rows)


if __name__ == "__main__":
    n = seed_competitions()
    with SessionLocal() as session:
        existing = (
            session.query(
                Competition.tier, Competition.name, Competition.type,
                Competition.fdcouk_key,
            )
            .order_by(Competition.tier.nulls_last())
            .all()
        )
    print(f"seeded/updated {n} competitions:")
    for tier, name, ctype, key in existing:
        tier_label = f"tier {tier}" if tier is not None else "knockout"
        print(f"  {tier_label}: {name} [{ctype}] (football-data.co.uk={key})")
