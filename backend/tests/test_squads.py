"""Tests for ESPN roster ingestion — Squad membership (ADR 0013).

The matching ladder is the risky part and the part that is pure enough to pin
down: a wrong match silently attributes one player's form to another, and a
missed match shows the same human twice (once as a no-data roster entry, once
via the 30-day union). Both are tested here. The HTTP fetch is injected.
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.facts import Squad
from app.models.reference import Player, Team
from ingestion.squads import (
    ESPN_PLAYER_ALIASES,
    RosterEntry,
    first_names_agree,
    global_name_index,
    match_player,
    refresh_squad,
    surname_key,
)


def _player(session, name: str, espn_id: str | None = None) -> Player:
    p = Player(canonical_name=name, espn_id=espn_id)
    session.add(p)
    session.flush()
    return p


# --- the matching ladder ---------------------------------------------------


def test_stored_espn_id_wins_over_any_name():
    """Once stamped, matching never depends on spelling again — the whole point
    of the id. Even a completely different display name must resolve."""
    with SessionLocal() as s:
        p = _player(s, "Andri Guðjohnsen", espn_id="12345")
        got = match_player(s, [p], RosterEntry("12345", "Someone Else Entirely", "F"))
        assert got is p
        s.rollback()


def test_exact_normalised_name_matches():
    with SessionLocal() as s:
        p = _player(s, "Max Bird")
        assert match_player(s, [p], RosterEntry("1", "Max Bird", "M")) is p
        s.rollback()


def test_nordic_letters_fold():
    """normalise_for_match strips combining marks, which handles é but NOT the
    standalone letters ð/ø — the exact reason Guðjohnsen and Lars-Jørgen
    Salvesen were missed in the spike."""
    with SessionLocal() as s:
        gud = _player(s, "Andri Guðjohnsen")
        sal = _player(s, "Lars-Jørgen Salvesen")
        assert match_player(s, [gud], RosterEntry("1", "Andri Gudjohnsen", "F")) is gud
        assert match_player(s, [sal], RosterEntry("2", "Lars-Jorgen Salvesen", "F")) is sal
        s.rollback()


def test_surname_matches_when_the_first_name_form_differs():
    """ESPN's full name vs our short name: 'Oladapo Afolayan' -> 'Dapo Afolayan',
    'Christian Forino Joseph' -> 'Christian Joseph'."""
    with SessionLocal() as s:
        dapo = _player(s, "Dapo Afolayan")
        joseph = _player(s, "Christian Joseph")
        assert match_player(s, [dapo], RosterEntry("1", "Oladapo Afolayan", "F")) is dapo
        assert match_player(
            s, [joseph], RosterEntry("2", "Christian Forino Joseph", "D")
        ) is joseph
        s.rollback()


def test_surname_rung_refuses_when_the_surname_is_not_unique():
    """Two Silvas at one club must never be merged. The surname rung is the
    loosest and only fires when it cannot be ambiguous."""
    with SessionLocal() as s:
        a = _player(s, "Thiago Silva")
        b = _player(s, "Bernardo Silva")
        assert match_player(s, [a, b], RosterEntry("1", "Danilo Silva", "D")) is None
        s.rollback()


def test_first_initial_disambiguates_a_shared_surname():
    """Surname+initial is tried BEFORE bare surname, so a shared surname still
    resolves when the initial is decisive."""
    with SessionLocal() as s:
        thiago = _player(s, "Thiago Silva")
        bernardo = _player(s, "Bernardo Silva")
        got = match_player(s, [thiago, bernardo], RosterEntry("1", "Thiago Silva", "D"))
        assert got is thiago
        s.rollback()


def test_alias_map_resolves_what_the_ladder_cannot(monkeypatch):
    """'Larra' vs 'Gaizka Larrazabal' shares no key at any rung — deterministic
    alias, never fuzzy."""
    with SessionLocal() as s:
        larra = _player(s, "Larra")
        assert match_player(s, [larra], RosterEntry("1", "Gaizka Larrazabal", "D")) is None

        monkeypatch.setitem(ESPN_PLAYER_ALIASES, "Gaizka Larrazabal", "Larra")
        assert match_player(s, [larra], RosterEntry("1", "Gaizka Larrazabal", "D")) is larra
        s.rollback()


def test_unmatched_returns_none_rather_than_guessing():
    with SessionLocal() as s:
        p = _player(s, "Max Bird")
        assert match_player(s, [p], RosterEntry("1", "Someone New", "F")) is None
        s.rollback()


def test_surname_key_is_the_last_token():
    assert surname_key("Lars-Jørgen Salvesen") == "salvesen"
    assert surname_key("Christian Forino Joseph") == "joseph"


# --- refresh_squad ---------------------------------------------------------


def test_refresh_squad_stamps_ids_and_writes_membership():
    """A matched player gets his espn_id stamped (so spelling never matters
    again) and a squads row; an unmatched roster entry is reported, not guessed."""
    with SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id).limit(1)).one()
        known = _player(s, "Zz Testcase Player")
        roster = [
            RosterEntry("900001", "Zz Testcase Player", "M"),
            RosterEntry("900002", "Zz Totally Unknown", "G"),
        ]

        report = refresh_squad(
            s, team, candidates=[known], fetch=lambda: roster, log=lambda _m: None
        )

        assert report["matched"] == 1
        assert report["unmatched"] == ["Zz Totally Unknown"]
        assert known.espn_id == "900001"
        row = s.scalar(
            select(Squad).where(
                Squad.team_id == team.id, Squad.player_id == known.id
            )
        )
        assert row is not None and row.active is True
        s.rollback()


def test_refresh_squad_deactivates_a_player_who_left():
    """A previous squad member absent from today's roster is marked inactive,
    not deleted — that is what makes a departure show up immediately."""
    with SessionLocal() as s:
        team = s.scalars(select(Team).order_by(Team.id).limit(1)).one()
        gone = _player(s, "Zz Departed Player", espn_id="900003")
        s.add(Squad(team_id=team.id, player_id=gone.id, active=True,
                    last_seen=dt.date(2026, 8, 1)))
        s.flush()

        refresh_squad(s, team, candidates=[gone], fetch=lambda: [], log=lambda _m: None)

        row = s.scalar(
            select(Squad).where(
                Squad.team_id == team.id, Squad.player_id == gone.id
            )
        )
        assert row is not None and row.active is False
        s.rollback()


def test_transfer_between_tracked_clubs_resolves_globally():
    """A player signed from another tracked club is on his NEW club's roster but
    has appearances only at the old one, so every club-scoped rung is blind to
    him — and he is off the old club's roster too, so he never gets stamped.
    Without a global rung he would show as a no-data member forever."""
    with SessionLocal() as s:
        incoming = _player(s, "Zz Transferred Keeper")
        index = global_name_index(s)

        # club-scoped only: invisible
        assert match_player(s, [], RosterEntry("1", "Zz Transferred Keeper", "G")) is None
        # with the global rung: found
        got = match_player(
            s, [], RosterEntry("1", "Zz Transferred Keeper", "G"), index
        )
        assert got is incoming
        s.rollback()


def test_global_rung_refuses_an_ambiguous_name():
    """Two stored players sharing a name must never be merged, even globally."""
    with SessionLocal() as s:
        _player(s, "Zz Duplicate Name")
        _player(s, "Zz Duplicate Name")
        index = global_name_index(s)

        got = match_player(s, [], RosterEntry("1", "Zz Duplicate Name", "M"), index)

        assert got is None
        s.rollback()


def test_global_rung_is_exact_name_only_never_surname():
    """Widening to surname at global scope would be reckless — thousands of
    players share one. The global rung is exact-full-name only."""
    with SessionLocal() as s:
        _player(s, "Zz Onlyone Uniquesurname")
        index = global_name_index(s)

        got = match_player(
            s, [], RosterEntry("1", "Someone Else Uniquesurname", "M"), index
        )

        assert got is None
        s.rollback()


def test_surname_rung_refuses_when_first_names_do_not_overlap():
    """The bug this guard exists for. Against real rosters a unique surname
    matched ESPN's 'Tom King' onto our Joshua King and 'Alfie Cresswell' onto
    our Charlie Cresswell — different people, and the espn_id stamp would have
    made it permanent and invisible."""
    with SessionLocal() as s:
        joshua = _player(s, "Joshua King")
        charlie = _player(s, "Charlie Cresswell")
        assert match_player(s, [joshua], RosterEntry("1", "Tom King", "F")) is None
        assert match_player(s, [charlie], RosterEntry("2", "Alfie Cresswell", "D")) is None
        s.rollback()


def test_surname_rung_still_allows_a_shortened_first_name():
    """'Dapo' really is 'Oladapo' — containment keeps the rung useful."""
    with SessionLocal() as s:
        dapo = _player(s, "Dapo Afolayan")
        assert match_player(s, [dapo], RosterEntry("1", "Oladapo Afolayan", "F")) is dapo
        s.rollback()


def test_mononym_matches_a_full_name():
    """Our 'Brau' against ESPN's 'Miguel Ángel Brau' — a single-token name has
    no first name to compare, so containment cannot apply."""
    with SessionLocal() as s:
        brau = _player(s, "Brau")
        assert match_player(s, [brau], RosterEntry("1", "Miguel Ángel Brau", "D")) is brau
        s.rollback()


def test_first_names_agree_is_containment_not_similarity():
    assert first_names_agree("Dapo Afolayan", "Oladapo Afolayan")
    assert first_names_agree("Brau", "Miguel Ángel Brau")
    assert not first_names_agree("Joshua King", "Tom King")
    assert not first_names_agree("Charlie Cresswell", "Alfie Cresswell")
    assert not first_names_agree("Bachir Belloumi", "Mohamed Belloumi")
