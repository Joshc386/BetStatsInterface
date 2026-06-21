"""Regression tests for the Summary Metric computation (needs team data loaded)."""

from sqlalchemy import select

from app.db import SessionLocal
from app.models.facts import TeamMatch
from app.stats import entity_summary


def _a_team_with_data(session) -> int:
    return session.scalar(
        select(TeamMatch.team_id).where(TeamMatch.competition_type == "club_league").limit(1)
    )


def test_team_summary_matches_direct_query():
    s = SessionLocal()
    try:
        tid = _a_team_with_data(s)
        res = entity_summary(s, entity="team", entity_id=tid, metric="corners", n=10)
        direct = [
            c for (c,) in s.execute(
                select(TeamMatch.corners)
                .where(TeamMatch.team_id == tid, TeamMatch.competition_type == "club_league")
                .order_by(TeamMatch.date.desc()).limit(10)
            ).all()
        ]
        assert res["games"] == len(direct)
        assert res["total"] == sum(direct)
        assert res["average"] == sum(direct) / len(direct)
    finally:
        s.close()


def test_going_in_excludes_latest_and_hitrate_is_bounded():
    s = SessionLocal()
    try:
        tid = _a_team_with_data(s)
        disp = entity_summary(s, entity="team", entity_id=tid, metric="corners", n=5)
        gin = entity_summary(s, entity="team", entity_id=tid, metric="corners", n=5,
                             window_mode="going_in")
        # going_in's most-recent game is no later than display's most-recent game
        assert gin["breakdown"][-1]["date"] <= disp["breakdown"][-1]["date"]
        hr = entity_summary(s, entity="team", entity_id=tid, metric="corners", n=10,
                            threshold=5)["hit_rate"]
        assert hr["n"] == 10 and 0 <= hr["hits"] <= 10
    finally:
        s.close()
