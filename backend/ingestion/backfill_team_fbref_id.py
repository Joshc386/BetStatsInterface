"""Phase A (ADR 0007) — backfill `teams.fbref_id` from the cached FBref pages.

Zero-network. Every cached match page embeds each squad's FBref id in its summary
table id (`stats_<fbref_id>_summary`), paired with the team name in the table's
`<caption>`. We scan the cache, resolve each FBref name to its canonical `teams`
row (explicit alias, then normalised-name — `match_existing_team`), and stamp
`fbref_id`. Idempotent and re-runnable: an already-correct id is left untouched;
a row that already holds a *different* id is reported, never overwritten.

Names that resolve to no canonical row (e.g. a European-cup opponent outside our
England universe) are listed as `unresolved`, not treated as an error — only the
~100 England clubs are in scope here. Run: `python -m ingestion.backfill_team_fbref_id`.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.reference import Team
from ingestion.players import _FBREF_CACHE, match_existing_team, parse_team_ids


def scan_cache_team_ids(
    cache_dir: Path = _FBREF_CACHE,
) -> tuple[dict[str, str], list[str]]:
    """Collect ``{fbref team name -> fbref_id}`` across every cached match page.

    Returns the mapping plus a list of conflict strings for any name FBref spells
    with two different ids (should never happen; surfaced loudly if it does).
    """
    mapping: dict[str, str] = {}
    conflicts: list[str] = []
    for page in sorted(cache_dir.glob("match_*.html")):
        try:
            teams = parse_team_ids(page.read_text(encoding="utf-8"))
        except Exception:  # a malformed/partial cached page — skip, keep scanning
            continue
        for name, fbref_id in teams.items():
            prior = mapping.get(name)
            if prior is None:
                mapping[name] = fbref_id
            elif prior != fbref_id:
                conflicts.append(
                    f"{name!r}: two ids {prior} vs {fbref_id} ({page.name})"
                )
    return mapping, conflicts


def backfill_fbref_ids(session: Session, name_to_id: dict[str, str]) -> dict:
    """Stamp `fbref_id` onto the canonical rows that `name_to_id` resolves to.

    Conflict-safe: never overwrites an existing different id, never assigns one
    id to two rows, never assigns two ids to one row in a single pass. Mutates
    the session (caller commits). Returns a report dict.
    """
    teams = list(session.scalars(select(Team)))
    id_owner: dict[str, Team] = {t.fbref_id: t for t in teams if t.fbref_id}
    assigned_this_run: dict[int, str] = {}

    populated = 0
    already = 0
    unresolved: list[str] = []
    conflicts: list[str] = []

    for name, fbref_id in sorted(name_to_id.items()):
        team = match_existing_team(session, name)
        if team is None:
            unresolved.append(name)
            continue

        owner = id_owner.get(fbref_id)
        if owner is not None and owner.id != team.id:
            conflicts.append(
                f"id {fbref_id} -> both {owner.canonical_name!r} and "
                f"{team.canonical_name!r} (via {name!r})"
            )
            continue
        if team.fbref_id == fbref_id:
            already += 1
            continue
        if team.fbref_id:  # a different id is already present — surface, don't clobber
            conflicts.append(
                f"{team.canonical_name!r} already has id {team.fbref_id}, "
                f"cache says {fbref_id} (via {name!r})"
            )
            continue
        prior = assigned_this_run.get(team.id)
        if prior and prior != fbref_id:
            conflicts.append(
                f"{team.canonical_name!r} got two ids this run: {prior} and "
                f"{fbref_id} (via {name!r})"
            )
            continue

        team.fbref_id = fbref_id
        id_owner[fbref_id] = team
        assigned_this_run[team.id] = fbref_id
        populated += 1

    still_missing = sorted(t.canonical_name for t in teams if not t.fbref_id)
    return {
        "teams_total": len(teams),
        "populated": populated,
        "already": already,
        "unresolved": sorted(set(unresolved)),
        "conflicts": conflicts,
        "still_missing": still_missing,
    }


def run(cache_dir: Path = _FBREF_CACHE) -> dict:
    """Scan the cache and backfill `teams.fbref_id` in one committed pass."""
    name_to_id, scan_conflicts = scan_cache_team_ids(cache_dir)
    with SessionLocal() as session:
        report = backfill_fbref_ids(session, name_to_id)
        session.commit()
    report["distinct_cache_names"] = len(name_to_id)
    report["scan_conflicts"] = scan_conflicts
    return report


if __name__ == "__main__":
    report = run()
    print(
        f"teams.fbref_id backfill (zero-network):\n"
        f"  distinct cache names: {report['distinct_cache_names']}\n"
        f"  teams in DB:          {report['teams_total']}\n"
        f"  newly populated:      {report['populated']}\n"
        f"  already set:          {report['already']}\n"
        f"  still missing id:     {len(report['still_missing'])}\n"
        f"  unresolved names:     {len(report['unresolved'])}\n"
        f"  scan conflicts:       {len(report['scan_conflicts'])}\n"
        f"  resolve conflicts:    {len(report['conflicts'])}"
    )
    for c in report["conflicts"][:20]:
        print("  CONFLICT:", c)
    if report["still_missing"]:
        print("  still missing:", ", ".join(report["still_missing"][:30]))
