"""Tests for upcoming-fixture ingestion from the ESPN scoreboard (ADR 0009).

Parsing and season derivation are pure (a checked-in trimmed sample of the real
JSON shape). Resolution and upsert tests run against the real DB in rolled-back
sessions — nothing is left behind.
"""

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.facts import Fixture
from app.models.reference import Competition, Team
from ingestion.upcoming import (
    ESPN_LEAGUES,
    FINISHED_STATUSES,
    UnknownEspnTeamError,
    parse_scoreboard,
    purge_stale_international_placeholders,
    resolve_espn_team,
    season_for,
    EUROPEAN_ESPN_SLUGS,
    european_pending_events,
    postponed_pairs,
    scoreboard_window,
    select_cup_events,
    stalled,
    takes_finished,
    upsert_event,
)

SAMPLE = json.loads(
    (Path(__file__).parent / "fixtures" / "espn_scoreboard_sample.json").read_text(
        encoding="utf-8"
    )
)


def test_parse_scoreboard_keeps_only_scheduled_events():
    """3 events in the sample, 1 already played -> 2 scheduled parsed rows."""
    events = parse_scoreboard(SAMPLE)
    assert len(events) == 2
    first = events[0]
    assert first.home_espn_id == "359" and first.away_espn_id == "388"
    assert first.home_names == ("Arsenal", "Arsenal")
    assert first.away_names == ("Coventry City", "Coventry")
    assert first.date == dt.datetime(2026, 8, 21, 19, 0, tzinfo=dt.timezone.utc)


def test_season_for_rolls_in_july():
    """English season code: July onward belongs to the season starting that year."""
    assert season_for(dt.datetime(2026, 8, 21, tzinfo=dt.timezone.utc)) == "2627"
    assert season_for(dt.datetime(2027, 5, 24, tzinfo=dt.timezone.utc)) == "2627"
    assert season_for(dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)) == "2526"
    assert season_for(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)) == "2627"


def test_espn_leagues_cover_the_four_tiers_world_cup_and_the_domestic_cups():
    assert set(ESPN_LEAGUES) == {
        "Premier League", "Championship", "League One", "League Two",
        "World Cup", "FA Cup", "EFL Cup",
    }


def test_domestic_cups_are_ingested_last():
    """Blast radius: an unresolved name rolls back its own competition and
    raises, so the cups — the only slate with a long tail of unfamiliar
    non-league clubs — must come after every league has committed."""
    assert list(ESPN_LEAGUES)[-2:] == ["FA Cup", "EFL Cup"]


def _wc_event(home: tuple[str, str, str], away: tuple[str, str, str],
              date: str = "2026-07-14T19:00Z") -> dict:
    """A minimal scheduled scoreboard event; sides are (id, display, short)."""
    def side(t, home_away):
        return {"homeAway": home_away,
                "team": {"id": t[0], "displayName": t[1], "shortDisplayName": t[2]}}
    return {
        "date": date,
        "status": {"type": {"name": "STATUS_SCHEDULED"}},
        "competitions": [{"competitors": [side(home, "home"), side(away, "away")],
                          "status": {"type": {"name": "STATUS_SCHEDULED"}}}],
    }


def test_parse_scoreboard_drops_undecided_knockout_slots():
    """ESPN models an undecided knockout side as a pseudo-team ('Quarterfinal 2
    Winner'); such events are dropped — the semi appears on the first run after
    the QF resolves it, never as a fixture with a fake team."""
    payload = {"events": [
        _wc_event(("164", "Spain", "Spain"), ("459", "Belgium", "Belgium")),
        _wc_event(("478", "France", "France"),
                  ("17629", "Quarterfinal 2 Winner", "QF W2")),
        _wc_event(("5958", "Semifinal 1 Loser", "SF L1"),
                  ("5959", "Semifinal 2 Loser", "SF L2")),
    ]}
    events = parse_scoreboard(payload)
    assert len(events) == 1
    assert events[0].home_names == ("Spain", "Spain")


def test_resolve_espn_team_by_name_stamps_id():
    """First contact: no espn_id stored -> matched by normalised name
    (display or short), and the ESPN id is stamped for id-first resolution
    next run. Rolled back."""
    with SessionLocal() as session:
        team = resolve_espn_team(session, "999359", ("Arsenal", "Arsenal"))
        assert team.canonical_name == "Arsenal"
        assert team.espn_id == "999359"
        session.rollback()


def test_resolve_espn_team_prefers_stored_id():
    """A stored espn_id wins regardless of what ESPN calls the team today."""
    with SessionLocal() as session:
        arsenal = session.scalars(
            select(Team).where(Team.canonical_name == "Arsenal")
        ).one()
        arsenal.espn_id = "359"
        session.flush()
        team = resolve_espn_team(session, "359", ("Renamed FC", "Renamed"))
        assert team.id == arsenal.id
        session.rollback()


def test_resolve_espn_team_unknown_name_fails_loud():
    with SessionLocal() as session:
        with pytest.raises(UnknownEspnTeamError):
            resolve_espn_team(session, "424242", ("Melchester Rovers", "Melchester"))
        session.rollback()


def test_upsert_scheduled_creates_then_updates_never_demotes():
    """A new event creates a scheduled fixture; a kick-off change updates it in
    place; once the fixture is finished the feed can no longer touch it.
    Rolled back."""
    with SessionLocal() as session:
        comp = session.scalars(
            select(Competition).where(Competition.name == "Premier League")
        ).one()
        teams = session.scalars(select(Team).limit(2)).all()
        home, away = teams[0], teams[1]
        d1 = dt.datetime(2026, 8, 21, 19, 0, tzinfo=dt.timezone.utc)

        assert upsert_event(session, comp, home.id, away.id, d1) == "created"
        fx = session.scalars(
            select(Fixture).where(
                Fixture.competition_id == comp.id,
                Fixture.season == "2627",
                Fixture.home_team_id == home.id,
                Fixture.away_team_id == away.id,
            )
        ).one()
        assert fx.status == "scheduled" and fx.date == d1

        d2 = d1 + dt.timedelta(days=1, hours=-3)  # TV reshuffle
        assert upsert_event(session, comp, home.id, away.id, d2) == "updated"
        session.refresh(fx)
        assert fx.date == d2 and fx.status == "scheduled"

        fx.status = "finished"
        session.flush()
        assert upsert_event(session, comp, home.id, away.id, d1) == "skipped_finished"
        session.refresh(fx)
        assert fx.status == "finished" and fx.date == d2  # untouched

        session.rollback()


def test_upsert_scheduled_international_uses_august_season_and_ignores_finished():
    """An international placeholder stores under the August-boundary season the
    FBref ingest will use (a July semi-final is season 2526, not 2627), and a
    finished meeting of the same pairing (a group game, stage-qualified) does
    NOT block it — the knockout rematch is a different game. Rolled back."""
    with SessionLocal() as session:
        comp = session.scalars(
            select(Competition).where(Competition.name == "World Cup")
        ).one()
        teams = session.scalars(select(Team).limit(2)).all()
        home, away = teams[0], teams[1]
        d = dt.datetime(2026, 7, 14, 19, 0, tzinfo=dt.timezone.utc)

        # a finished group meeting with the same orientation already exists
        session.add(Fixture(
            competition_id=comp.id, season="2526", date=d - dt.timedelta(days=20),
            home_team_id=home.id, away_team_id=away.id,
            status="finished", stage="Group stage",
        ))
        session.flush()

        assert upsert_event(session, comp, home.id, away.id, d) == "created"
        placeholder = session.scalars(
            select(Fixture).where(
                Fixture.competition_id == comp.id,
                Fixture.home_team_id == home.id,
                Fixture.away_team_id == away.id,
                Fixture.status == "scheduled",
            )
        ).one()
        assert placeholder.season == "2526"  # August boundary, not season_for's 2627
        session.rollback()


def test_purge_removes_only_past_scheduled_internationals():
    """The purge deletes this competition's date-passed placeholders and nothing
    else: future placeholders and finished rows survive. Rolled back."""
    with SessionLocal() as session:
        comp = session.scalars(
            select(Competition).where(Competition.name == "World Cup")
        ).one()
        teams = session.scalars(select(Team).limit(4)).all()
        now = dt.datetime(2026, 7, 16, tzinfo=dt.timezone.utc)

        past = Fixture(competition_id=comp.id, season="2526",
                       date=now - dt.timedelta(days=1),
                       home_team_id=teams[0].id, away_team_id=teams[1].id,
                       status="scheduled")
        future = Fixture(competition_id=comp.id, season="2526",
                         date=now + dt.timedelta(days=3),
                         home_team_id=teams[2].id, away_team_id=teams[3].id,
                         status="scheduled")
        finished = Fixture(competition_id=comp.id, season="2526",
                           date=now - dt.timedelta(days=2),
                           home_team_id=teams[1].id, away_team_id=teams[0].id,
                           status="finished", stage="Group stage")
        session.add_all([past, future, finished])
        session.flush()

        # assert on OUR three rows only — the real DB may hold live placeholders
        # that are also (correctly) purged relative to the synthetic `now`
        assert purge_stale_international_placeholders(session, comp, now) >= 1
        remaining = session.scalars(
            select(Fixture).where(Fixture.id.in_([past.id, future.id, finished.id]))
        ).all()
        assert {f.id for f in remaining} == {future.id, finished.id}
        session.rollback()


# --- cup slate: finished events + covered-tie filter (ADR 0012) ------------
# Cups have no football-data.co.uk feed, so nothing marks a cup tie played and
# matchday's pending probe was structurally blind. ESPN supplies that signal.


def test_finished_statuses_cover_extra_time_and_penalties():
    """Cups are exactly where 90 minutes is not the end. The 2026-08-21 spike
    found all three of these in a single EFL Cup window; matching only
    STATUS_FINAL would miss every tie that went past full time."""
    assert {
        "STATUS_FULL_TIME",
        "STATUS_FINAL_AET",
        "STATUS_FINAL_PEN",
    } <= FINISHED_STATUSES


def test_parse_scoreboard_ignores_finished_events_by_default():
    """Unchanged for leagues: fd.co.uk owns their results, not ESPN."""
    assert all(not e.finished for e in parse_scoreboard(SAMPLE))
    assert len(parse_scoreboard(SAMPLE)) == 2


def test_parse_scoreboard_includes_finished_when_asked():
    """3 events in the sample: 2 scheduled + 1 STATUS_FULL_TIME."""
    events = parse_scoreboard(SAMPLE, include_finished=True)
    assert len(events) == 3
    played = [e for e in events if e.finished]
    assert len(played) == 1
    assert played[0].home_names[0] == "Everton"
    assert played[0].date == dt.datetime(2026, 8, 15, 14, 0, tzinfo=dt.timezone.utc)


def test_upsert_event_creates_a_finished_cup_tie():
    """A cup tie already played when first seen is created finished outright —
    there was never a scheduled row to promote."""
    with SessionLocal() as session:
        comp = session.scalar(select(Competition).where(Competition.name == "FA Cup"))
        home, away = session.scalars(select(Team).limit(2)).all()
        date = dt.datetime(2099, 1, 10, 15, 0, tzinfo=dt.timezone.utc)

        assert upsert_event(session, comp, home.id, away.id, date, finished=True) == "created"

        fixture = session.scalar(
            select(Fixture).where(
                Fixture.competition_id == comp.id,
                Fixture.home_team_id == home.id,
                Fixture.away_team_id == away.id,
                Fixture.season == "9899",
            )
        )
        assert fixture.status == "finished"
        session.rollback()


def test_upsert_event_promotes_a_scheduled_cup_tie_once_played():
    """The reuse-in-place path: domestic cups key on stage='', the same key
    cups.get_or_create_cup_fixture uses, so one row serves both sources."""
    with SessionLocal() as session:
        comp = session.scalar(select(Competition).where(Competition.name == "EFL Cup"))
        home, away = session.scalars(select(Team).limit(2)).all()
        date = dt.datetime(2099, 8, 12, 18, 45, tzinfo=dt.timezone.utc)

        assert upsert_event(session, comp, home.id, away.id, date) == "created"
        assert upsert_event(session, comp, home.id, away.id, date, finished=True) == "finished"

        rows = session.scalars(
            select(Fixture).where(
                Fixture.competition_id == comp.id,
                Fixture.home_team_id == home.id,
                Fixture.away_team_id == away.id,
                Fixture.season == season_for(date),
            )
        ).all()
        assert len(rows) == 1 and rows[0].status == "finished"
        session.rollback()


def test_select_cup_events_keeps_only_covered_ties():
    """ESPN serves every tie in the competition, including the non-league early
    rounds. Only ties with a covered club are ours; the rest are dropped without
    resolving the opponent, so an unfamiliar club is never alias work."""
    from ingestion.cups import covered_team_ids
    from ingestion.upcoming import ScheduledEvent

    with SessionLocal() as session:
        covered = covered_team_ids(session, "2526")
        covered_team = session.scalar(select(Team).where(Team.id.in_(covered)))
        outsider = session.scalar(select(Team).where(Team.id.notin_(covered)))
        date = dt.datetime(2099, 1, 10, 15, 0, tzinfo=dt.timezone.utc)

        def event(home, away):
            return ScheduledEvent(
                date=date,
                home_espn_id=str(home.espn_id or f"x{home.id}"),
                away_espn_id=str(away.espn_id or f"x{away.id}"),
                home_names=(home.canonical_name, home.canonical_name),
                away_names=(away.canonical_name, away.canonical_name),
                finished=True,
            )

        keep, unresolved = select_cup_events(
            session, [event(covered_team, outsider), event(outsider, outsider)], "2526"
        )

        assert len(keep) == 1
        assert keep[0][0].home_names[0] == covered_team.canonical_name
        assert unresolved == []
        session.rollback()


def test_window_reaches_backwards_whenever_finished_events_are_taken():
    """A played match is in the PAST. A forward-only window steps straight over a
    match played at 19:45 the evening before the 08:00 run, so the slate would
    never once observe it finished — the whole point of reading it.

    Leagues need this as much as cups do (ADR 0014): before it, only
    football-data.co.uk ever marked a league Fixture finished, so an unpublished
    CSV silently withdrew the Fixture from FBref's player pipeline too."""
    today = dt.date(2026, 8, 21)

    forward_start, forward_end = scoreboard_window(today, 45, lookback=False)
    assert forward_start == today
    assert forward_end == dt.date(2026, 10, 5)

    back_start, back_end = scoreboard_window(today, 45, lookback=True)
    assert back_start < today
    assert back_end == forward_end
    # last night's match must be inside the window
    assert back_start <= today - dt.timedelta(days=1)


def test_leagues_and_cups_take_finished_events_internationals_never_do():
    """The international exception is load-bearing, not an oversight.

    ADR 0011 placeholders are ephemeral, and
    `purge_stale_international_placeholders` deletes only *scheduled* past rows —
    so a placeholder marked finished would never be purged and would linger as
    exactly the ghost that function exists to prevent. Its real finished row
    comes from the FBref ingest under a stage-qualified key."""
    assert takes_finished("club_league") is True
    assert takes_finished("club_cup") is True
    assert takes_finished("international") is False


# --- European signal (ADR 0012): reads ESPN, writes NOTHING ----------------


def test_european_slugs_are_the_main_competitions_never_qualifying():
    """Qualifying is deliberately absent. The FBref-sourced European data holds
    no qualifying stage at all across its 502 fixtures, so a qualifying tie
    would be pending work FBref can never satisfy - alarming every day forever."""
    assert set(EUROPEAN_ESPN_SLUGS.values()) == {
        "uefa.champions", "uefa.europa", "uefa.europa.conf",
    }
    assert not any("qual" in slug for slug in EUROPEAN_ESPN_SLUGS.values())
    # and the signal map must stay OUT of the row-writing map
    assert not (set(EUROPEAN_ESPN_SLUGS) & set(ESPN_LEAGUES))


def _european_event(home, away, date, finished=True):
    from ingestion.upcoming import ScheduledEvent

    return ScheduledEvent(
        date=date,
        home_espn_id=str(home.espn_id or f"x{home.id}"),
        away_espn_id=str(away.espn_id or f"x{away.id}"),
        home_names=(home.canonical_name, home.canonical_name),
        away_names=(away.canonical_name, away.canonical_name),
        finished=finished,
    )


def test_european_pending_ignores_ties_we_already_hold():
    """A covered club's tie that is already ingested is not pending work."""
    from ingestion.cups import covered_team_ids

    with SessionLocal() as session:
        comp = session.scalar(
            select(Competition).where(Competition.name == "Champions League")
        )
        existing = session.scalars(
            select(Fixture)
            .where(Fixture.competition_id == comp.id)
            .order_by(Fixture.id)
            .limit(1)
        ).one()
        covered = covered_team_ids(session, existing.season)
        if existing.home_team_id not in covered and existing.away_team_id not in covered:
            pytest.skip("sample fixture has no covered side in its season")
        home = session.get(Team, existing.home_team_id)
        away = session.get(Team, existing.away_team_id)

        pending = european_pending_events(
            session, comp, [_european_event(home, away, existing.date)], existing.season
        )

        assert pending == []


def test_european_pending_flags_a_covered_tie_we_do_not_hold():
    """Same covered club, a date we hold nothing for -> pending work."""
    from ingestion.cups import covered_team_ids

    with SessionLocal() as session:
        comp = session.scalar(
            select(Competition).where(Competition.name == "Champions League")
        )
        existing = session.scalars(
            select(Fixture)
            .where(Fixture.competition_id == comp.id)
            .order_by(Fixture.id)
            .limit(1)
        ).one()
        covered = covered_team_ids(session, existing.season)
        if existing.home_team_id not in covered and existing.away_team_id not in covered:
            pytest.skip("sample fixture has no covered side in its season")
        home = session.get(Team, existing.home_team_id)
        away = session.get(Team, existing.away_team_id)
        far_off = existing.date + dt.timedelta(days=900)

        pending = european_pending_events(
            session, comp, [_european_event(home, away, far_off)], existing.season
        )

        assert len(pending) == 1


def test_european_pending_ignores_ties_with_no_covered_club():
    """Foreign-vs-foreign is not ours, and must never become alias work."""
    from ingestion.cups import covered_team_ids

    with SessionLocal() as session:
        comp = session.scalar(
            select(Competition).where(Competition.name == "Champions League")
        )
        covered = covered_team_ids(session, "2526")
        outsiders = session.scalars(
            select(Team).where(Team.id.notin_(covered)).limit(2)
        ).all()
        date = dt.datetime(2099, 3, 1, tzinfo=dt.timezone.utc)

        pending = european_pending_events(
            session, comp, [_european_event(*outsiders, date)], "2526"
        )

        assert pending == []


def test_european_pending_ignores_unfinished_events():
    """A tie that has not been played is not pending ingestion work."""
    from ingestion.cups import covered_team_ids

    with SessionLocal() as session:
        comp = session.scalar(
            select(Competition).where(Competition.name == "Champions League")
        )
        covered = covered_team_ids(session, "2526")
        team = session.scalar(select(Team).where(Team.id.in_(covered)))
        other = session.scalar(select(Team).where(Team.id.notin_(covered)))
        date = dt.datetime(2099, 3, 1, tzinfo=dt.timezone.utc)

        pending = european_pending_events(
            session, comp, [_european_event(team, other, date, finished=False)], "2526"
        )

        assert pending == []


# --- plumbing backstop (ADR 0014) ------------------------------------------


def _ev(home_id, away_id, status):
    return {
        "date": "2026-08-21T19:00Z",
        "status": {"type": {"name": status}},
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"id": home_id, "displayName": "H",
                                                  "shortDisplayName": "H"}},
                    {"homeAway": "away", "team": {"id": away_id, "displayName": "A",
                                                  "shortDisplayName": "A"}},
                ]
            }
        ],
    }


def test_postponed_pairs_picks_out_only_called_off_events():
    payload = {
        "events": [
            _ev("1", "2", "STATUS_POSTPONED"),
            _ev("3", "4", "STATUS_FULL_TIME"),
            _ev("5", "6", "STATUS_SCHEDULED"),
            _ev("7", "8", "STATUS_ABANDONED"),
        ]
    }
    assert postponed_pairs(payload) == {("1", "2"), ("7", "8")}


NOW_STALL = dt.datetime(2026, 8, 23, 7, 30, tzinfo=dt.timezone.utc)


def test_a_long_past_unmarked_fixture_is_stalled():
    """The ADR 0014 failure itself: nothing marked it played, so the coverage
    audit — which only looks at FINISHED fixtures — can never see it."""
    played = NOW_STALL - dt.timedelta(days=2)
    assert stalled([("1", "2", played)], set(), now=NOW_STALL) == [("1", "2", played)]


def test_a_postponed_fixture_is_not_stalled():
    """It genuinely was not played. `scheduled` is the correct status for it."""
    called_off = NOW_STALL - dt.timedelta(days=2)
    assert stalled([("1", "2", called_off)], {("1", "2")}, now=NOW_STALL) == []


def test_last_nights_match_is_not_yet_stalled():
    """The same run that sees it finished marks it — no alarm on the way past."""
    last_night = NOW_STALL - dt.timedelta(hours=12)
    assert stalled([("1", "2", last_night)], set(), now=NOW_STALL) == []
