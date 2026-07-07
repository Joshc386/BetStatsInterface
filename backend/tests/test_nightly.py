"""Tests for the unattended nightly refresh (Phase 5, tier 1).

Both steps hit the network, so we monkeypatch them and assert the orchestration
contract: it targets the *current* season and it *commits* points adjustments
(apply=True) — the two things a silent regression could get wrong.
"""

import datetime as dt

from ingestion import nightly


def test_nightly_refreshes_current_season_and_applies_points(monkeypatch):
    calls: dict = {}

    def fake_team_ingest(seasons):
        calls["team_seasons"] = seasons
        return {"per_league_season": {f"Premier League {seasons[0]}": 10},
                "skipped": [], "fixtures": 10, "team_match": 20}

    def fake_points(*, apply, log=print):
        calls["points_apply"] = apply
        return [{"team": "Everton", "points": -8}]

    monkeypatch.setattr(nightly.team_match, "ingest", fake_team_ingest)
    monkeypatch.setattr(
        nightly.points_adjustments, "ingest_points_adjustments", fake_points
    )

    # A February 2027 run belongs to season 2627.
    result = nightly.run_nightly(
        now=dt.datetime(2027, 2, 1, tzinfo=dt.timezone.utc), log=lambda *a, **k: None
    )

    assert calls["team_seasons"] == ["2627"]  # current season only, not a full backfill
    assert calls["points_apply"] is True  # writes, never a dry run
    assert result["season"] == "2627"
    assert result["points"] == [{"team": "Everton", "points": -8}]
