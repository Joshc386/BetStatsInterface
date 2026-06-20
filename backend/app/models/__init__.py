"""Importing this package registers every model on `Base.metadata`
(used by Alembic's `target_metadata`)."""

from app.models.facts import Fixture, PlayerMatch, Squad, TeamMatch
from app.models.reference import Competition, Player, Team

__all__ = [
    "Competition",
    "Team",
    "Player",
    "Fixture",
    "TeamMatch",
    "PlayerMatch",
    "Squad",
]
