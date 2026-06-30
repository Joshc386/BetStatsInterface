"""Tests for the zero-network teams.fbref_id backfill (Phase A, ADR 0007).

`scan_cache_team_ids` is tested against tiny synthetic pages in a tmp dir; the
DB-touching tests run in rolled-back sessions and leave nothing behind.
"""

from ingestion.backfill_team_fbref_id import (
    backfill_fbref_ids,
    scan_cache_team_ids,
)
from app.db import SessionLocal
from app.models.reference import Team


def _page(*tables: tuple[str, str]) -> str:
    """A minimal match page carrying the given (fbref_id, caption_name) squads."""
    body = "".join(
        f'<table id="stats_{fid}_summary">'
        f"<caption>{name} Player Stats Table</caption></table>"
        for fid, name in tables
    )
    return f"<html><body>{body}</body></html>"


def test_scan_cache_team_ids_aggregates_across_pages(tmp_path):
    (tmp_path / "match_1.html").write_text(
        _page(("aaaaaaaa", "Alpha"), ("bbbbbbbb", "Beta")), encoding="utf-8"
    )
    (tmp_path / "match_2.html").write_text(
        _page(("aaaaaaaa", "Alpha"), ("cccccccc", "Gamma")), encoding="utf-8"
    )
    mapping, conflicts = scan_cache_team_ids(tmp_path)
    assert mapping == {"Alpha": "aaaaaaaa", "Beta": "bbbbbbbb", "Gamma": "cccccccc"}
    assert conflicts == []


def test_scan_cache_team_ids_flags_a_name_with_two_ids(tmp_path):
    (tmp_path / "match_1.html").write_text(
        _page(("aaaaaaaa", "Alpha")), encoding="utf-8"
    )
    (tmp_path / "match_2.html").write_text(
        _page(("dddddddd", "Alpha")), encoding="utf-8"
    )
    mapping, conflicts = scan_cache_team_ids(tmp_path)
    assert len(conflicts) == 1 and "Alpha" in conflicts[0]


def test_backfill_populates_and_is_idempotent():
    session = SessionLocal()
    try:
        t = Team(canonical_name="__ZZ Backfill Town__", fdcouk_name="__ZZ Backfill Town__")
        session.add(t)
        session.flush()

        r1 = backfill_fbref_ids(session, {"__ZZ Backfill Town__": "zzbf0001"})
        assert t.fbref_id == "zzbf0001"
        assert r1["populated"] == 1 and not r1["conflicts"]

        # second pass is a no-op (already populated), never a duplicate
        r2 = backfill_fbref_ids(session, {"__ZZ Backfill Town__": "zzbf0001"})
        assert r2["populated"] == 0 and r2["already"] == 1
    finally:
        session.rollback()
        session.close()


def test_backfill_reports_conflict_not_overwrite():
    session = SessionLocal()
    try:
        t = Team(canonical_name="__ZZ Conflict Town__", fbref_id="zzcf0001")
        session.add(t)
        session.flush()

        report = backfill_fbref_ids(session, {"__ZZ Conflict Town__": "zzcf9999"})
        assert t.fbref_id == "zzcf0001"  # NOT overwritten
        assert report["populated"] == 0 and len(report["conflicts"]) == 1
    finally:
        session.rollback()
        session.close()


def test_backfill_lists_out_of_universe_names_as_unresolved():
    session = SessionLocal()
    try:
        report = backfill_fbref_ids(session, {"__ZZ Not In Our Universe__": "zznu0001"})
        assert report["populated"] == 0
        assert "__ZZ Not In Our Universe__" in report["unresolved"]
    finally:
        session.rollback()
        session.close()
