"""Tests for the pre-backfill mapping check.

The check exists to stop a 28-hour backfill starting on a false premise, so its
own failure mode matters more than most: a checker that reports "safe" after
checking nothing is worse than no checker, because the iron rule ("never start a
backfill until this reports 0 gaps") is then satisfied by a vacuous pass.

Both tests below are regressions from 2026-08-24, when the League One check
printed "OK - every team name resolves. Safe to backfill." having read 0 names.
"""

from ingestion import verify_team_aliases
from ingestion.players import LEAGUE_IDS
from ingestion.verify_team_aliases import _LEAGUE_KEYS, verify


def test_every_backfillable_league_has_a_schedule_key():
    """_LEAGUE_KEYS was a third copy of the league list and silently fell behind.

    When League One / Two joined LEAGUE_IDS this map still held the top two, so
    `league_key` resolved to None, the schedule scan was skipped entirely, and
    the six cached League One schedules were never read.
    """
    assert set(_LEAGUE_KEYS) == set(LEAGUE_IDS)
    assert _LEAGUE_KEYS == LEAGUE_IDS


def test_checking_nothing_is_not_a_pass(monkeypatch, capsys):
    """Zero names checked must never exit 0 — that is the false green."""
    monkeypatch.setattr(verify_team_aliases, "_schedule_names", lambda key: set())
    monkeypatch.setattr(
        verify_team_aliases, "_match_page_names", lambda s, c: (set(), 0, 0)
    )

    exit_code = verify("Premier League", "ENG-Premier League")

    assert exit_code != 0
    assert "Safe to backfill" not in capsys.readouterr().out


def test_resolving_schedule_names_alone_is_not_a_pass(monkeypatch, capsys):
    """The second half of the same false green, and the subtler half.

    Names WERE read and every one of them resolved — but all of them came from
    the schedule, because no match page was cached. The player-df spellings are
    the ones that actually stop a backfill (they differ from the schedule's:
    that is what FBREF_TEAM_ALIASES exists for), so this reports 0 gaps having
    verified none of the spellings that matter.
    """
    monkeypatch.setattr(
        verify_team_aliases, "_schedule_names", lambda key: {"Arsenal", "Chelsea"}
    )
    monkeypatch.setattr(
        verify_team_aliases, "_match_page_names", lambda s, c: (set(), 0, 0)
    )

    exit_code = verify("Premier League", "ENG-Premier League")

    out = capsys.readouterr().out
    assert exit_code != 0
    assert "Safe to backfill" not in out
    assert "NO match pages were cached" in out
