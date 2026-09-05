"""Regression tests for the Summary Metric computation (needs team data loaded)."""

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import team_summary
from app.models.facts import PlayerMatch, TeamMatch
from app.stats import entity_summary, registry


def _a_team_with_data(session) -> int:
    # ORDER BY is load-bearing: an unordered LIMIT 1 picks whichever row the
    # heap happens to yield, so a migration that rewrites the table silently
    # changes which team these tests exercise.
    return session.scalar(
        select(TeamMatch.team_id)
        .where(TeamMatch.competition_type == "club_league")
        .order_by(TeamMatch.team_id)
        .limit(1)
    )


def _a_player_with_data(session) -> int:
    """Same ORDER BY reasoning as above. Skips rather than returning quietly:
    a test that does nothing must not report as a pass."""
    pid = session.scalar(
        select(PlayerMatch.player_id).order_by(PlayerMatch.player_id).limit(1)
    )
    if pid is None:
        pytest.skip("no player data loaded in this environment")
    return pid


def test_team_summary_matches_direct_query():
    """entity_summary defaults to ALL competitions, so the hand-written query it
    is checked against must not filter to club_league — that mismatch made this
    test pass or fail on whether the chosen team happened to have a cup tie in
    its last 10. NULLs are excluded from total/average but still counted as
    games, which is stats.py's actual contract (a sparse row shrinks the sample
    rather than scoring zero)."""
    s = SessionLocal()
    try:
        tid = _a_team_with_data(s)
        res = entity_summary(s, entity="team", entity_id=tid, metric="corners", n=10)
        direct = [
            c for (c,) in s.execute(
                select(TeamMatch.corners)
                .where(TeamMatch.team_id == tid)
                .order_by(TeamMatch.date.desc()).limit(10)
            ).all()
        ]
        values = [c for c in direct if c is not None]
        assert res["games"] == len(direct)
        assert res["total"] == sum(values)
        assert res["average"] == sum(values) / len(values)
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
        pid = _a_player_with_data(s)
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
        # Most-recent season with MORE THAN ONE league game. The point of this
        # test is that n=1 does not clamp, which needs a season of >1 game to
        # demonstrate -- and in the opening rounds of a new season the newest
        # tag has exactly one, so picking it blindly fails every August.
        season = s.scalar(
            select(TeamMatch.season)
            .where(TeamMatch.team_id == tid, TeamMatch.competition_type == "club_league")
            .group_by(TeamMatch.season)
            .having(func.count() > 1)
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
        pid = _a_player_with_data(s)
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


def test_team_endpoint_threads_is_home_through():
    """The /teams/{id}/summary endpoint exposes is_home and threads it to
    entity_summary (call the handler directly; FastAPI Query defaults don't
    resolve off-server, so pass every param explicitly)."""
    s = SessionLocal()
    try:
        tid = _a_team_with_data(s)
        home = team_summary(
            team_id=tid, metric="corners", n=100, competition_id=None, scope="club_league",
            seasons=None, is_home=True, threshold=None, direction="over",
            window_mode="display", session=s,
        )
        assert home.games > 0
        assert all(r.is_home for r in home.breakdown)
    finally:
        s.close()


def test_competition_filter_narrows_to_a_single_competition():
    s = SessionLocal()
    try:
        # a team that has played in more than one competition
        # team_id is the tie-break: many clubs share the top competition count,
        # so ordering on the count alone still leaves the winner up to the heap.
        tid, _ = s.execute(
            select(TeamMatch.team_id, func.count(func.distinct(TeamMatch.competition_id)))
            .group_by(TeamMatch.team_id)
            .order_by(
                func.count(func.distinct(TeamMatch.competition_id)).desc(),
                TeamMatch.team_id,
            )
            .limit(1)
        ).one()
        comp = s.scalar(
            select(TeamMatch.competition_id)
            .where(TeamMatch.team_id == tid)
            .order_by(TeamMatch.competition_id)
            .limit(1)
        )
        all_comp = entity_summary(s, entity="team", entity_id=tid, metric="total_goals", n=500)
        one_comp = entity_summary(s, entity="team", entity_id=tid, metric="total_goals", n=500,
                                  competition_id=comp)
        assert 0 < one_comp["games"] < all_comp["games"]  # strictly narrower
    finally:
        s.close()


# --- ADR 0016: aggregates divide by Recorded Appearances -----------------------
#
# A NULL Metric means the source did not publish the column for that match --
# never that the player registered zero (it is all-or-nothing per PAGE; no
# fixture is ever partly recorded). So a sparse row must shrink the sample
# rather than score zero, which is the contract test_team_summary_matches_
# direct_query has asserted for `average` since this module was written.
#
# Synthetic rows in a rolled-back session, deliberately: a fixed input with a
# hand-computed expected output is the project rule for statistical code, and
# the expected values then cannot drift as real data lands.

_RECORDED = [(90, 3), (45, 1), (90, 5)]   # (minutes, shots) -- shots published
_UNRECORDED = [90, 90]                    # minutes only; shots NULL

# Hand-computed from the rows above, so a refactor cannot quietly move them:
_TOTAL = 9                 # 3 + 1 + 5
_GAMES = 5                 # every Appearance in the window
_MINUTES_TOTAL = 405       # 90 + 45 + 90 + 90 + 90
_RECORDED_GAMES = 3        # Appearances whose page published `shots`
_RECORDED_MINUTES = 225    # 90 + 45 + 90
_PER_APPEARANCE = 3.0      # 9 / 3      (the bug gave 9 / 5 = 1.8)
_PER_90 = 3.6              # 9 / 225 * 90   (the bug gave 9 / 405 * 90 = 2.0)


def _synthetic_player(session, rows):
    """A throwaway Player with one Appearance per supplied (minutes, shots).

    Rides real Fixtures so the joins in entity_summary are exercised for real;
    `shots=None` reproduces an unpublished column. Caller rolls back.
    """
    from app.models.facts import Fixture
    from app.models.reference import Competition, Player

    fixtures = session.execute(
        select(
            Fixture.id, Fixture.competition_id, Fixture.season, Fixture.date,
            Fixture.home_team_id, Fixture.away_team_id,
        )
        .join(Competition, Competition.id == Fixture.competition_id)
        .where(Competition.type == "club_league")
        .order_by(Fixture.id)
        .limit(len(rows))
    ).all()
    if len(fixtures) < len(rows):
        pytest.skip("not enough fixtures loaded to build the synthetic window")

    player = Player(canonical_name="__adr0016_test_player__")
    session.add(player)
    session.flush()

    for (fid, comp_id, season, date, home_id, away_id), (minutes, shots) in zip(
        fixtures, rows
    ):
        session.add(
            PlayerMatch(
                fixture_id=fid, competition_id=comp_id,
                competition_type="club_league", season=season, date=date,
                player_id=player.id, team_id=home_id, opponent_id=away_id,
                is_home=True, minutes=minutes, shots=shots, goals=1,
            )
        )
    session.flush()
    return player.id


def test_per_90_divides_by_recorded_minutes_not_every_minute():
    """The tracer bullet. Minutes played in a match whose page never published
    `shots` are not minutes over which shots can be counted, so including them
    understates the rate -- by 32-64% for real players with caps in minor
    -confederation qualifiers, where 95.6% of unpublished pages live."""
    with SessionLocal() as session:
        pid = _synthetic_player(
            session, _RECORDED + [(m, None) for m in _UNRECORDED]
        )

        res = entity_summary(session, entity="player", entity_id=pid, metric="shots")

        assert res["total"] == _TOTAL
        assert res["per_90"] == pytest.approx(_PER_90)

        session.rollback()


def test_per_appearance_divides_by_recorded_appearances():
    """Same rule one level up: an Appearance whose page never published the
    Metric is not an Appearance the Metric can be averaged over. It still counts
    as an Appearance for goals, cards and minutes, which WERE published."""
    with SessionLocal() as session:
        pid = _synthetic_player(
            session, _RECORDED + [(m, None) for m in _UNRECORDED]
        )

        res = entity_summary(session, entity="player", entity_id=pid, metric="shots")

        assert res["per_appearance"] == pytest.approx(_PER_APPEARANCE)

        session.rollback()


def test_the_denominators_used_are_published_beside_the_figures():
    """Without this the payload contradicts itself: total / minutes_total * 90
    no longer reproduces per_90, and nothing on screen explains the gap. Same
    reason hit-rate has always reported its own "of N"."""
    with SessionLocal() as session:
        pid = _synthetic_player(
            session, _RECORDED + [(m, None) for m in _UNRECORDED]
        )

        res = entity_summary(session, entity="player", entity_id=pid, metric="shots")

        assert res["recorded_games"] == _RECORDED_GAMES
        assert res["recorded_minutes"] == _RECORDED_MINUTES
        # the published denominators ARE the ones used -- checkable by hand
        assert res["total"] / res["recorded_games"] == pytest.approx(res["per_appearance"])
        assert res["total"] / res["recorded_minutes"] * 90 == pytest.approx(res["per_90"])

        session.rollback()


def test_the_window_is_metric_independent_but_its_sample_is_not():
    """Two counts that are both true and need not match. The same five
    Appearances give shots a smaller sample than goals, because the pages that
    omitted the shot columns still published minutes and goals -- but `games`
    and `minutes_total` describe the WINDOW, so they must not move when the
    metric changes, or the window itself looks unstable."""
    with SessionLocal() as session:
        pid = _synthetic_player(
            session, _RECORDED + [(m, None) for m in _UNRECORDED]
        )

        shots = entity_summary(session, entity="player", entity_id=pid, metric="shots")
        goals = entity_summary(session, entity="player", entity_id=pid, metric="goals")

        # the window is the same window, whichever Metric you ask about
        assert shots["games"] == goals["games"] == _GAMES
        assert shots["minutes_total"] == goals["minutes_total"] == _MINUTES_TOTAL

        # the sample is not: goals was published in every one of them
        assert shots["recorded_games"] == _RECORDED_GAMES
        assert goals["recorded_games"] == _GAMES
        assert goals["recorded_minutes"] == _MINUTES_TOTAL

        session.rollback()


def test_average_is_team_vocabulary_and_absent_for_players():
    """Once per_appearance divides by Recorded Appearances the two are the same
    number, and CONTEXT.md has only ever given players total / per-Appearance /
    per-90. So `average` joins per_appearance, per_90 and minutes_total as an
    entity-specific field rather than sitting in the payload as a second name
    for a figure that already has one."""
    with SessionLocal() as session:
        pid = _synthetic_player(
            session, _RECORDED + [(m, None) for m in _UNRECORDED]
        )
        player = entity_summary(session, entity="player", entity_id=pid, metric="shots")
        assert player["average"] is None
        assert player["per_appearance"] is not None
        session.rollback()

    with SessionLocal() as session:
        tid = _a_team_with_data(session)
        team = entity_summary(session, entity="team", entity_id=tid, metric="corners")
        assert team["average"] is not None
        assert team["per_appearance"] is None      # teams have no Appearances
        assert team["recorded_minutes"] is None    # nor minutes


def test_no_team_figure_moves_only_its_denominator_becomes_visible():
    """Teams were already correct -- `average` has always divided by non-NULL
    values. 2,716 team_match rows (7.9%) carry a NULL Metric, so team averages
    were already computed over fewer games than `games` reported; ADR 0016 only
    publishes that count. This guards the 'only'."""
    with SessionLocal() as session:
        tid = _a_team_with_data(session)
        res = entity_summary(session, entity="team", entity_id=tid, metric="corners",
                             n=10)
        direct = [
            c for (c,) in session.execute(
                select(TeamMatch.corners)
                .where(TeamMatch.team_id == tid)
                .order_by(TeamMatch.date.desc()).limit(10)
            ).all()
        ]
        values = [c for c in direct if c is not None]

        assert res["games"] == len(direct)                    # unchanged
        assert res["total"] == sum(values)                    # unchanged
        assert res["average"] == sum(values) / len(values)    # unchanged
        assert res["recorded_games"] == len(values)           # newly visible


def test_a_window_with_nothing_recorded_yields_no_rate_not_a_zero():
    """The degenerate case the exclusion rule creates: every page in the window
    omitted the Metric, so there is no sample at all. That must read as "we do
    not know" (None), never as a rate of zero -- which is the very fabrication
    ADR 0016 exists to stop. The Appearances themselves stay real and counted."""
    with SessionLocal() as session:
        pid = _synthetic_player(session, [(90, None), (75, None), (60, None)])

        res = entity_summary(session, entity="player", entity_id=pid, metric="shots")

        assert res["games"] == 3                # he did play
        assert res["minutes_total"] == 225      # and these minutes are real
        assert res["recorded_games"] == 0       # but nothing was recorded
        assert res["recorded_minutes"] == 0
        assert res["per_appearance"] is None
        assert res["per_90"] is None
        assert res["total"] == 0                # sum of an empty set, as before

        session.rollback()
