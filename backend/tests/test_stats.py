"""Regression tests for the Summary Metric computation (needs team data loaded)."""

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.facts import PlayerMatch, TeamMatch
from app.stats import entity_summary, registry


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


def test_goals_assists_registered_and_computable():
    # Migration 0003 added player goals/assists columns; they must be exposed as
    # metrics and aggregate like any other count metric.
    assert "goals" in registry("player")
    assert "assists" in registry("player")
    s = SessionLocal()
    try:
        pid = s.scalar(select(PlayerMatch.player_id).limit(1))
        if pid is None:
            return  # no player data loaded in this environment
        res = entity_summary(s, entity="player", entity_id=pid, metric="goals", n=10)
        direct = [
            g for (g,) in s.execute(
                select(PlayerMatch.goals)
                .where(PlayerMatch.player_id == pid)
                .order_by(PlayerMatch.date.desc()).limit(10)
            ).all()
        ]
        assert res["games"] == len(direct)
        assert res["total"] == sum(g for g in direct if g is not None)
    finally:
        s.close()


def test_season_window_filters_to_chosen_seasons_and_ignores_n():
    """Season mode is a filter over the season tag, not the last-N window: it must
    return *every* row in the chosen season(s) regardless of n, and stay scope-clean."""
    s = SessionLocal()
    try:
        tid = _a_team_with_data(s)
        # pick the most-recent season this team has league rows in
        season = s.scalar(
            select(TeamMatch.season)
            .where(TeamMatch.team_id == tid, TeamMatch.competition_type == "club_league")
            .order_by(TeamMatch.season.desc()).limit(1)
        )
        res = entity_summary(s, entity="team", entity_id=tid, metric="corners",
                             scope="club_league", n=1, seasons=[season])
        direct = [
            c for (c,) in s.execute(
                select(TeamMatch.corners).where(
                    TeamMatch.team_id == tid,
                    TeamMatch.competition_type == "club_league",
                    TeamMatch.season == season,
                )
            ).all()
        ]
        # n=1 must NOT clamp the result — season mode ignores it
        assert res["games"] == len(direct) > 1
        assert res["total"] == sum(c for c in direct if c is not None)
        assert all(season in res["window"] for season in [season])

        # multi-season superset: two seasons >= one season
        seasons2 = [
            ss for (ss,) in s.execute(
                select(TeamMatch.season).distinct()
                .where(TeamMatch.team_id == tid, TeamMatch.competition_type == "club_league")
                .order_by(TeamMatch.season.desc()).limit(2)
            ).all()
        ]
        multi = entity_summary(s, entity="team", entity_id=tid, metric="corners",
                               scope="club_league", n=1, seasons=seasons2)
        assert multi["games"] >= res["games"]
    finally:
        s.close()


def test_venue_split_partitions_and_opponent_narrows():
    """Home and away must be a clean partition of all games, and an opponent
    filter must isolate exactly that opponent's meetings (filter-then-window)."""
    s = SessionLocal()
    try:
        pid = s.scalar(select(PlayerMatch.player_id).limit(1))
        if pid is None:
            return  # no player data loaded in this environment
        allv = entity_summary(s, entity="player", entity_id=pid, metric="shots", n=10000)
        home = entity_summary(s, entity="player", entity_id=pid, metric="shots", n=10000,
                              is_home=True)
        away = entity_summary(s, entity="player", entity_id=pid, metric="shots", n=10000,
                              is_home=False)
        assert home["games"] + away["games"] == allv["games"]
        assert all(r["is_home"] for r in home["breakdown"])
        assert all(not r["is_home"] for r in away["breakdown"])

        opp_id = allv["breakdown"][0]["opponent_id"]
        vs = entity_summary(s, entity="player", entity_id=pid, metric="shots", n=10000,
                            opponent_id=opp_id)
        assert 0 < vs["games"] <= allv["games"]
        assert all(r["opponent_id"] == opp_id for r in vs["breakdown"])
    finally:
        s.close()


def test_competition_filter_narrows_to_a_single_competition():
    s = SessionLocal()
    try:
        # a team that has played in more than one competition
        tid, _ = s.execute(
            select(TeamMatch.team_id, func.count(func.distinct(TeamMatch.competition_id)))
            .group_by(TeamMatch.team_id)
            .order_by(func.count(func.distinct(TeamMatch.competition_id)).desc())
            .limit(1)
        ).first()
        comp = s.scalar(select(TeamMatch.competition_id).where(TeamMatch.team_id == tid).limit(1))
        all_comp = entity_summary(s, entity="team", entity_id=tid, metric="total_goals", n=500)
        one_comp = entity_summary(s, entity="team", entity_id=tid, metric="total_goals", n=500,
                                  competition_id=comp)
        assert 0 < one_comp["games"] < all_comp["games"]  # strictly narrower
    finally:
        s.close()
