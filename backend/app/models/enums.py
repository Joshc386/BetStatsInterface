"""Domain enums and their shared SQLAlchemy enum-type bindings.

The SQLAlchemy `Enum` objects use `create_type=False`: the Postgres enum types
are created explicitly in the initial migration, not auto-emitted by the ORM.
Reusing one shared object per type avoids duplicate `CREATE TYPE` attempts when
the same enum appears on multiple tables.
"""

import enum

from sqlalchemy import Enum as SAEnum


class CompetitionType(str, enum.Enum):
    club_league = "club_league"
    club_cup = "club_cup"
    club_european = "club_european"
    international = "international"


class FixtureStatus(str, enum.Enum):
    scheduled = "scheduled"
    finished = "finished"


class MatchResult(str, enum.Enum):
    W = "W"
    D = "D"
    L = "L"


competition_type_enum = SAEnum(
    CompetitionType, name="competition_type", create_type=False
)
fixture_status_enum = SAEnum(FixtureStatus, name="fixture_status", create_type=False)
match_result_enum = SAEnum(MatchResult, name="match_result", create_type=False)
