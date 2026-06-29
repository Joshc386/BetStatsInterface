"""Player-view segmentation tests (Spell support).

Function-level against the real DB, test_stats.py style. The player view groups a
player's appearances into Spells (by club) / by competition; both need the club +
competition on each breakdown row, and a team_id filter to isolate one Spell.
Anchor case: Adam Armstrong (multiple clubs + competitions in the data).
"""

from sqlalchemy import select

from app.db import SessionLocal
from app.main import player_summary
from app.models.facts import PlayerMatch
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


def test_min_minutes_filters_then_windows():
    """A min_minutes floor keeps only appearances of >= that many minutes, applied
    before the last-N window — so every breakdown row clears the floor and the
    filtered window has no more games than the unfiltered one."""
    s = SessionLocal()
    try:
        pid = _multiclub_player(s)
        floor = 60
        full = entity_summary(s, entity="player", entity_id=pid, metric="goals", n=50)
        filtered = entity_summary(
            s, entity="player", entity_id=pid, metric="goals", n=50, min_minutes=floor
        )
        assert filtered["breakdown"], "expected some qualifying appearances"
        assert all(r["minutes"] >= floor for r in filtered["breakdown"])
        assert filtered["games"] <= full["games"]
    finally:
        s.close()


def test_min_minutes_matches_a_direct_query_oracle():
    """Known-output regression: the filtered figure equals a direct query over the
    same appearances (last n with minutes >= floor). per_appearance / per_90
    recompute over the filtered set, so a cameo cannot inflate the rate."""
    s = SessionLocal()
    try:
        pid = _multiclub_player(s)
        floor, n = 60, 20

        # the floor must actually exclude something for the test to mean anything
        all_rows = entity_summary(s, entity="player", entity_id=pid, metric="goals", n=1000)
        assert any(r["minutes"] < floor for r in all_rows["breakdown"]), \
            "anchor must have a sub-floor appearance"

        res = entity_summary(
            s, entity="player", entity_id=pid, metric="goals", n=n, min_minutes=floor
        )

        oracle = s.execute(
            select(PlayerMatch.goals, PlayerMatch.minutes)
            .where(PlayerMatch.player_id == pid, PlayerMatch.minutes >= floor)
            .order_by(PlayerMatch.date.desc())
            .limit(n)
        ).all()
        exp_total = sum(g for g, _ in oracle if g is not None)
        exp_minutes = sum(m for _, m in oracle if m is not None)
        exp_games = len(oracle)

        assert res["games"] == exp_games
        assert res["total"] == exp_total
        assert res["minutes_total"] == exp_minutes
        assert res["per_appearance"] == exp_total / exp_games
        assert res["per_90"] == exp_total / exp_minutes * 90
    finally:
        s.close()


def test_min_minutes_zero_is_unchanged_behaviour():
    """Default floor (0) must leave the result identical to omitting min_minutes —
    entity_summary is the regression oracle, so the default cannot drift."""
    s = SessionLocal()
    try:
        pid = _multiclub_player(s)
        base = entity_summary(s, entity="player", entity_id=pid, metric="shots_on_target", n=30)
        zero = entity_summary(
            s, entity="player", entity_id=pid, metric="shots_on_target", n=30, min_minutes=0
        )
        assert zero["games"] == base["games"]
        assert zero["total"] == base["total"]
        assert [r["date"] for r in zero["breakdown"]] == [r["date"] for r in base["breakdown"]]
    finally:
        s.close()


def test_endpoint_threads_min_minutes_through():
    """The /players/{id}/summary endpoint exposes min_minutes and threads it to
    entity_summary (call the handler directly — FastAPI Query defaults don't
    resolve off-server, so pass every param explicitly, test_squad.py style)."""
    s = SessionLocal()
    try:
        pid = _multiclub_player(s)
        result = player_summary(
            player_id=pid, metric="goals", n=20, competition_id=None, scope=None,
            season=None, team_id=None, min_minutes=60, threshold=None,
            direction="over", window_mode="display", session=s,
        )
        oracle = entity_summary(
            s, entity="player", entity_id=pid, metric="goals", n=20, min_minutes=60
        )
        assert result.games == oracle["games"]
        assert result.total == oracle["total"]
        assert all(r.minutes >= 60 for r in result.breakdown)
    finally:
        s.close()
