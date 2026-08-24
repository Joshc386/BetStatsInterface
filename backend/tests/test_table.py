"""Known-output regression tests for the computed league table (ADR 0010).

Pinned to externally published final standings so a refactor can't silently
change the computation: PL 2023-24 (Everton -8, Forest -4 deductions applied)
and Derby's -21 Championship 2021-22. Read-only against the live DB.
"""

import datetime as dt

from sqlalchemy import select

from app.db import SessionLocal
from app.models.reference import Competition
from app.table import league_seasons, league_table


def _competition_id(session, name: str) -> int:
    return session.scalars(
        select(Competition.id).where(Competition.name == name)
    ).one()


def _row(table: list[dict], team_name: str) -> dict:
    return next(r for r in table if r["team_name"] == team_name)


def test_pl_2324_final_table_matches_published_standings():
    with SessionLocal() as session:
        table = league_table(
            session, competition_id=_competition_id(session, "Premier League"),
            season="2324",
        )
    assert len(table) == 20
    assert table[0]["team_name"] == "Man City" and table[0]["points"] == 91

    everton = _row(table, "Everton")  # 48 on results, 40 after the -8
    assert everton["played"] == 38
    assert (everton["won"], everton["drawn"], everton["lost"]) == (13, 9, 16)
    assert (everton["gf"], everton["ga"], everton["gd"]) == (40, 51, -11)
    assert everton["adjustment"] == -8 and everton["points"] == 40
    assert everton["position"] == 15
    assert "Profitability and Sustainability" in everton["adjustment_note"]

    forest = _row(table, "Nott'm Forest")  # 36 on results, 32 after the -4
    assert forest["adjustment"] == -4 and forest["points"] == 32


def test_derby_2122_deduction_applied():
    with SessionLocal() as session:
        table = league_table(
            session, competition_id=_competition_id(session, "Championship"),
            season="2122",
        )
    derby = _row(table, "Derby")  # 55 on results, 34 after the famous -21
    assert derby["adjustment"] == -21 and derby["points"] == 34


def test_table_invariants_hold_every_league_season():
    """W+D+L = played, sum(GF) = sum(GA), and positions are 1..n in points
    order — across every ingested league season."""
    with SessionLocal() as session:
        comps = session.execute(
            select(Competition.id, Competition.name).where(
                Competition.type == "club_league"
            )
        ).all()
        for comp_id, name in comps:
            from app.table import league_seasons  # local: also under test

            for season in league_seasons(session, comp_id):
                table = league_table(session, competition_id=comp_id, season=season)
                assert table, f"{name} {season}: empty table"
                assert [r["position"] for r in table] == list(
                    range(1, len(table) + 1)
                )
                assert sum(r["gf"] for r in table) == sum(r["ga"] for r in table)
                for r in table:
                    assert r["won"] + r["drawn"] + r["lost"] == r["played"]
                    assert (
                        r["points"]
                        == 3 * r["won"] + r["drawn"] + r["adjustment"]
                    )
                pts = [(r["points"], r["gd"], r["gf"]) for r in table]
                assert pts == sorted(pts, reverse=True), f"{name} {season} order"


def test_table_endpoint_defaults_and_guards():
    """Handler called directly (no TestClient here): season defaults to the
    latest ingested, and cups are rejected — a cup has no league table."""
    import pytest
    from fastapi import HTTPException

    from app.main import table

    with SessionLocal() as session:
        comp_id = _competition_id(session, "Premier League")

        # The default is the latest season we hold rows for, computed rather
        # than named: this used to assert "20 clubs on 38 games" because the
        # latest happened to be the completed 2526, which stopped being true
        # the moment 2026-27 team data arrived (docs/adr/0015).
        latest = league_seasons(session, comp_id)[-1]
        rows = table(
            competition_id=comp_id, season=None, as_of=None, session=session
        )
        expected = league_table(session, competition_id=comp_id, season=latest)
        assert [r.position for r in rows] == list(range(1, len(rows) + 1))
        assert [r.team_name for r in rows] == [r["team_name"] for r in expected]

        # A COMPLETED season is the stable shape check — 20 clubs, 38 games.
        done = table(
            competition_id=comp_id, season="2526", as_of=None, session=session
        )
        assert len(done) == 20 and {r.played for r in done} == {38}

        fa_cup = session.scalars(
            select(Competition.id).where(Competition.name == "FA Cup")
        ).one()
        with pytest.raises(HTTPException) as exc:
            table(competition_id=fa_cup, season=None, as_of=None, session=session)
        assert exc.value.status_code == 422


def test_as_of_bounds_the_window():
    with SessionLocal() as session:
        comp_id = _competition_id(session, "Premier League")
        full = league_table(session, competition_id=comp_id, season="2324")
        mid = league_table(
            session, competition_id=comp_id, season="2324",
            as_of=dt.date(2024, 1, 1),
        )
        pre = league_table(
            session, competition_id=comp_id, season="2324",
            as_of=dt.date(2023, 7, 1),
        )
    assert all(r["played"] == 38 for r in full)
    assert len(mid) == 20 and all(r["played"] < 38 for r in mid)
    assert pre == []
