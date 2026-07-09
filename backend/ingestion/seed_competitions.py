"""Seed the v1 competitions (top 4 English tiers + Championship play-offs).

Idempotent: upserts on the unique competition name. Run after migrations:
    python -m ingestion.seed_competitions
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models.reference import Competition

# name, type, country, tier, fdcouk_key, fbref_key
#   tier is the English-pyramid rank (league only); None for the knockouts.
#   country is None for the UEFA competitions (not national competitions).
#   fdcouk_key is None where football-data.co.uk doesn't cover the competition.
#   fbref_key is None where the competition has no standalone FBref page — the
#   play-offs are sourced from the Championship schedule (round = "play-offs").
COMPETITIONS = [
    ("Premier League", "club_league", "England", 1, "E0", "Premier League"),
    ("Championship", "club_league", "England", 2, "E1", "Championship"),
    ("League One", "club_league", "England", 3, "E2", None),
    ("League Two", "club_league", "England", 4, "E3", None),
    # Promotion play-offs: 3rd-6th play two-legged semis + a Wembley final.
    # Domestic knockout (club_cup) so it never counts as league form; player
    # data only (football-data.co.uk has no play-off coverage).
    ("Championship Play-offs", "club_cup", "England", None, None, None),
    # Domestic cups (ADR 0008): player-only, FBref-sourced (fbref_key set;
    # football-data.co.uk does not cover cups, so fdcouk_key=None). Distinct
    # competition_id keeps "FA Cup form" and "EFL Cup form" separable.
    ("FA Cup", "club_cup", "England", None, None, "FA Cup"),
    ("EFL Cup", "club_cup", "England", None, None, "EFL Cup"),
    # European club competitions (ADR 0011): covered ties only, FBref-sourced.
    # fbref_key is the exact name on FBref's comps index (verified from cache).
    ("Champions League", "club_european", None, None, None, "UEFA Champions League"),
    ("Europa League", "club_european", None, None, None, "UEFA Europa League"),
    ("Conference League", "club_european", None, None, None, "UEFA Conference League"),
    ("UEFA Super Cup", "club_european", None, None, None, "UEFA Super Cup"),
    # International competitions (ADR 0011): whole-competition (no covered-tie
    # filter), FBref-sourced, player data + zero-network team rows. country=None
    # (multi-nation tournaments, like the UEFA comps). fbref_key is the exact
    # FBref comps-index name.
    ("World Cup", "international", None, None, None, "FIFA World Cup"),
    ("Euros", "international", None, None, None, "UEFA European Football Championship"),
    # ASCII display name (batch-arg-safe on Windows); accented form stays the
    # fbref_key where soccerdata matches it.
    ("Copa America", "international", None, None, None, "CONMEBOL Copa América"),
    ("AFCON", "international", None, None, None, "Africa Cup of Nations"),
    ("Asian Cup", "international", None, None, None, "AFC Asian Cup"),
    ("Gold Cup", "international", None, None, None, "CONCACAF Gold Cup"),
    ("Nations League", "international", None, None, None, "UEFA Nations League"),
    # Qualifiers (ADR 0011 update 2026-07-09, via ingestion.fbref_shim). ONE
    # "World Cup Qualifiers" competition for all confederations + the
    # inter-confederation play-offs (8 FBref pages -> fbref_key=None, the
    # play-offs precedent); the other qualifying campaigns are 1:1. Dual-badged
    # AFC matches land here, not under Asian Cup Qualifying (CONTEXT.md).
    ("World Cup Qualifiers", "international", None, None, None, None),
    ("Euros Qualifying", "international", None, None, None, "UEFA Euro qualification"),
    ("AFCON Qualifying", "international", None, None, None, "Africa Cup of Nations qualification"),
    ("Asian Cup Qualifying", "international", None, None, None, "AFC Asian Cup qualification"),
]


def seed_competitions() -> int:
    rows = [
        {
            "name": name,
            "type": ctype,
            "country": country,
            "tier": tier,
            "fdcouk_key": fdcouk_key,
            "fbref_key": fbref_key,
        }
        for (name, ctype, country, tier, fdcouk_key, fbref_key) in COMPETITIONS
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
