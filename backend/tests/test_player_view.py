"""Player-view segmentation tests (Spell support).

Function-level against the real DB, test_stats.py style. The player view groups a
player's appearances into Spells (by club) / by competition; both need the club +
competition on each breakdown row, and a team_id filter to isolate one Spell.
Anchor case: Adam Armstrong (multiple clubs + competitions in the data).
"""

from sqlalchemy import select

from app.db import SessionLocal
from app.models.reference import Player
from app.stats import entity_summary


def _multiclub_player(session) -> int:
    pid = session.scalar(
        select(Player.id).where(Player.canonical_name == "Adam Armstrong")
    )
    assert pid is not None, "expected Adam Armstrong in the player data"
    return pid


def test_player_breakdown_rows_carry_the_club():
    s = SessionLocal()
    try:
        pid = _multiclub_player(s)
        res = entity_summary(s, entity="player", entity_id=pid, metric="goals", n=50)
        assert res["breakdown"], "expected appearances"
        row = res["breakdown"][0]
        # the club the player turned out for that game — needed for Spell grouping
        assert "team" in row and row["team"]
        assert "team_id" in row and isinstance(row["team_id"], int)
    finally:
        s.close()


def test_team_id_filter_isolates_one_spell():
    s = SessionLocal()
    try:
        pid = _multiclub_player(s)
        full = entity_summary(s, entity="player", entity_id=pid, metric="goals", n=1000)
        teams = {r["team_id"] for r in full["breakdown"]}
        assert len(teams) >= 2, "anchor player must span clubs"

        one = sorted(teams)[0]
        spell = entity_summary(
            s, entity="player", entity_id=pid, metric="goals", n=1000, team_id=one
        )
        assert spell["breakdown"]
        assert all(r["team_id"] == one for r in spell["breakdown"])  # only that club
        assert spell["games"] < full["games"]  # isolating a spell never invents games
    finally:
        s.close()


def test_per_team_subtotals_reconcile_to_total():
    """The whole segmentation contract: grouping the window's rows by club and
    summing gives back the headline total (they partition the same rows)."""
    s = SessionLocal()
    try:
        from collections import defaultdict

        pid = _multiclub_player(s)
        res = entity_summary(s, entity="player", entity_id=pid, metric="goals", n=1000)
        by_team: dict[int, int] = defaultdict(int)
        for r in res["breakdown"]:
            if r["value"] is not None:
                by_team[r["team_id"]] += r["value"]
        assert len(by_team) >= 2
        assert sum(by_team.values()) == res["total"]
    finally:
        s.close()
