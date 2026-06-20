"""Seed the four v1 competitions (top 4 English tiers, all club_league).

Idempotent: upserts on the unique competition name. Run after migrations:
    python -m ingestion.seed_competitions
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models.reference import Competition

# name, tier, fdcouk_key, fbref_key (None where FBref linkage is deferred to Phase 4)
COMPETITIONS = [
    ("Premier League", 1, "E0", "Premier League"),
    ("Championship", 2, "E1", "Championship"),
    ("League One", 3, "E2", None),
    ("League Two", 4, "E3", None),
]


def seed_competitions() -> int:
    rows = [
        {
            "name": name,
            "type": "club_league",
            "country": "England",
            "tier": tier,
            "fdcouk_key": fdcouk_key,
            "fbref_key": fbref_key,
        }
        for (name, tier, fdcouk_key, fbref_key) in COMPETITIONS
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
            session.query(Competition.tier, Competition.name, Competition.fdcouk_key)
            .order_by(Competition.tier)
            .all()
        )
    print(f"seeded/updated {n} competitions:")
    for tier, name, key in existing:
        print(f"  tier {tier}: {name} (football-data.co.uk={key})")
