"""Pre-backfill team-name mapping check — covers BOTH FBref spellings.

FBref spells clubs two ways and the backfill hits both:
  - the SCHEDULE page (often short: "QPR", "West Brom", "Sheffield Weds")
  - each PLAYER-MATCH page scorebox (full: "Queens Park Rangers", ...)

`resolve_fbref_team` is fail-loud, so a single unmapped spelling silently skips
every fixture involving that club. This module resolves every team name from both
sources against the canonical `teams` table and reports all gaps at once, with a
fuzzy canonical suggestion — so aliases are fixed completely before (or between)
backfill runs, not discovered one painful season at a time.

Run:  python -m ingestion.verify_team_aliases "Championship"
Exit 0 = every name maps; exit 1 = gaps (printed with suggestions).
"""

from __future__ import annotations

import difflib
import sys
from pathlib import Path

from lxml import html as lxml_html
from sqlalchemy import select

from app.db import SessionLocal
from app.models.facts import Fixture
from app.models.reference import Competition, Team
from ingestion.players import (
    UnknownTeamError,
    canonical_team_name,
    resolve_fbref_team,
)

_CACHE = Path.home() / "soccerdata" / "data" / "FBref"


def _schedule_names(league_key: str) -> set[str]:
    """Home/away names from every cached schedule page for the league."""
    names: set[str] = set()
    for page in _CACHE.glob(f"schedule_{league_key}_*.html"):
        doc = lxml_html.fromstring(page.read_bytes())
        for stat in ("home_team", "away_team"):
            for cell in doc.xpath(f"//td[@data-stat='{stat}']"):
                if (txt := cell.text_content().strip()):
                    names.add(txt)
    return names


def _scorebox_names(match_html: bytes) -> list[str]:
    """The two team names from a match page scorebox (== player-df team spelling)."""
    doc = lxml_html.fromstring(match_html)
    return [a.text.strip() for a in doc.xpath("//div[@class='scorebox']//strong/a")[:2] if a.text]


def _match_page_names(session, competition: Competition) -> tuple[set[str], int, int]:
    """Team names from cached match pages for this competition's fixtures.

    Every club appears throughout the fixture list, so the name set stabilises
    quickly; we stop once no new name has appeared in STABLE consecutive pages
    (bounds the scan — reading every page is slow under on-access AV scanning).
    """
    STABLE = 60  # stop a season once this many consecutive pages add no new name
    rows = session.execute(
        select(Fixture.season, Fixture.fbref_match_id)
        .where(
            Fixture.competition_id == competition.id,
            Fixture.fbref_match_id.is_not(None),
        )
        .order_by(Fixture.season)
    ).all()
    # group game_ids per season so every season's clubs are sampled (promotion/
    # relegation means each season has a different set), and stop each season once
    # its name set stabilises — bounds the scan under slow on-access AV.
    by_season: dict[str, list[str]] = {}
    for season, gid in rows:
        by_season.setdefault(season, []).append(gid)

    names: set[str] = set()
    cached = 0
    for season, gids in by_season.items():
        since_new = 0
        for gid in gids:
            page = _CACHE / f"match_{gid}.html"
            if not page.exists():
                continue
            cached += 1
            before = len(names)
            names.update(_scorebox_names(page.read_bytes()))
            since_new = 0 if len(names) > before else since_new + 1
            if since_new >= STABLE:
                break
        print(f"  {season}: {len(names)} distinct names so far ({cached} pages)", flush=True)
    return names, cached, len(rows)


def verify(competition_name: str, league_key: str | None = None) -> int:
    with SessionLocal() as session:
        competition = session.scalar(
            select(Competition).where(Competition.name == competition_name)
        )
        if competition is None:
            print(f"competition {competition_name!r} not seeded")
            return 2
        canon = [c for (c,) in session.execute(select(Team.canonical_name)).all()]

        print("scanning schedule + cached match pages…", flush=True)
        sched = _schedule_names(league_key) if league_key else set()
        match_names, cached, total = _match_page_names(session, competition)
        print(f"schedule names: {len(sched)} | match-page names: {len(match_names)} "
              f"(from {cached}/{total} cached match pages)")
        if cached == 0:
            print("WARNING: no cached match pages — player-df spellings unverified. "
                  "Run a small backfill first, then re-check.")

        all_names = sorted(sched | match_names)
        gaps: list[tuple[str, str]] = []
        for name in all_names:
            try:
                resolve_fbref_team(session, name)
            except UnknownTeamError:
                suggestion = difflib.get_close_matches(
                    canonical_team_name(name), canon, n=1, cutoff=0.4
                )
                gaps.append((name, suggestion[0] if suggestion else "?"))

    print(f"\nchecked {len(all_names)} unique team names across both sources")
    if not gaps:
        print("OK — every team name resolves. Safe to backfill.")
        return 0
    print(f"GAPS ({len(gaps)}) — add to FBREF_TEAM_ALIASES before backfill:")
    for name, suggestion in gaps:
        print(f'    "{name}": "{suggestion}",')
    return 1


# competition -> soccerdata league key (for the schedule-page glob)
_LEAGUE_KEYS = {
    "Premier League": "ENG-Premier League",
    "Championship": "ENG-Championship",
}


if __name__ == "__main__":
    comp = sys.argv[1] if len(sys.argv) > 1 else "Championship"
    sys.exit(verify(comp, _LEAGUE_KEYS.get(comp)))
