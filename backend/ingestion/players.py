"""Phase 4 — FBref player-match ingestion.

FBref's ``read_player_match_stats('summary', match_id=...)`` keys players by
display *name* only — there is no stable id in the DataFrame. But the match HTML
that soccerdata caches to disk embeds the FBref player id on every summary-table
row (``data-append-csv``). We recover that id here so ``players`` are upserted on
``players.fbref_id`` (stable across spelling drift and same-name collisions),
rather than on the lossy display name.

Pure helpers (HTML/df parsing, name reconciliation) are unit-tested against the
cached pages; the DB upserts are idempotent and commit per match (resumable).

NOTE: live FBref fetches require the VPN OFF (Cloudflare blocks the VPN exit IP).
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
from lxml import html as lxml_html
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.facts import Fixture, PlayerMatch
from app.models.reference import Competition, Player, Team
from ingestion.names import clean_name

# soccerdata writes each fetched match page here; we re-read it to recover the
# per-player FBref ids that read_player_match_stats drops.
_FBREF_CACHE = Path.home() / "soccerdata" / "data" / "FBref"


class UnknownTeamError(Exception):
    """An FBref team name resolved to no canonical `teams` row — add an alias."""


class PlayerIdError(Exception):
    """A summary-df player name had no id in the parsed HTML map (join gap)."""

# FBref display name -> canonical_name (the cleaned football-data.co.uk spelling
# already in `teams`). Only the names that DIFFER need an entry; everything else
# resolves by identity. FBref spells teams TWO ways: short names in `read_schedule`
# (e.g. "Manchester Utd") and full club names in the player-match df (e.g.
# "Manchester United") — both must be covered. Any FBref name missing here AND
# absent from `teams` makes resolve_fbref_team raise — fail loud, never invent a
# phantom team. (Confirmed-from-cache entries are noted; the rest are FBref's
# standard full names, and fail-loud catches any surprise during backfill.)
FBREF_TEAM_ALIASES: dict[str, str] = {
    # schedule short names
    "Manchester Utd": "Man United",
    "Nottingham": "Nott'm Forest",
    # player-match df full names (✓ = observed in a cached match page)
    "Manchester United": "Man United",  # ✓
    "Brighton & Hove Albion": "Brighton",  # ✓
    "Wolverhampton Wanderers": "Wolves",  # ✓
    "Ipswich Town": "Ipswich",  # ✓ (also the schedule spelling)
    "Newcastle United": "Newcastle",
    "Tottenham Hotspur": "Tottenham",
    "West Ham United": "West Ham",
    "Nottingham Forest": "Nott'm Forest",
    # spelt identically in both sources, but still differ from canonical
    "Manchester City": "Man City",
    "Leeds United": "Leeds",
    "Leicester City": "Leicester",
    "Luton Town": "Luton",  # promoted for 2023-24
}


def canonical_team_name(fbref_name: str) -> str:
    """Map an FBref team name to its canonical (football-data) spelling.

    Applies the explicit alias when one exists, otherwise returns the cleaned
    name unchanged (most clubs share the same spelling across sources).
    """
    name = clean_name(fbref_name)
    return FBREF_TEAM_ALIASES.get(name, name)


def resolve_fbref_team(session: Session, fbref_name: str) -> Team:
    """Return the canonical Team for an FBref team name.

    Raises UnknownTeamError if no canonical row matches — we never create teams
    from FBref (the canonical universe comes from football-data.co.uk); a miss
    means a missing alias, which must be fixed, not silently papered over.
    """
    canonical = canonical_team_name(fbref_name)
    team = session.scalar(select(Team).where(Team.canonical_name == canonical))
    if team is None:
        raise UnknownTeamError(
            f"FBref team {fbref_name!r} -> {canonical!r} not found in teams; "
            "add an entry to FBREF_TEAM_ALIASES."
        )
    return team


# player_match field -> summary DataFrame column (MultiIndex tuple). `second_yellows`
# and `xg` are intentionally absent: FBref 1.9.0's summary carries neither.
_MIN_COL = ("min", "")
_STAT_COLS: dict[str, tuple[str, str]] = {
    "goals": ("Performance", "Gls"),
    "assists": ("Performance", "Ast"),
    "shots": ("Performance", "Sh"),
    "sot": ("Performance", "SoT"),
    "tackles": ("Performance", "TklW"),  # tackles WON (FBref dropped total Tkl)
    "fouls_drawn": ("Performance", "Fld"),
    "fouls_committed": ("Performance", "Fls"),
    "yellows": ("Performance", "CrdY"),
    "reds": ("Performance", "CrdR"),
}


def _to_int(value) -> int | None:
    """Coerce a stat cell (float / <NA> / blank) to int or None."""
    try:
        if value is None or pd.isna(value):
            return None
        return int(value)
    except (ValueError, TypeError):
        return None


def summary_to_player_stats(df: pd.DataFrame) -> list[dict]:
    """Flatten one match's `summary` DataFrame into per-appearance metric dicts.

    Each dict carries the FBref `team` and `player` names (resolved/joined to ids
    by the caller) plus the player_match metric fields. Rows with no minutes are
    skipped — they are not an appearance.
    """
    names = list(df.index.names)
    ti, pi = names.index("team"), names.index("player")
    out: list[dict] = []
    for idx, row in df.iterrows():
        minutes = _to_int(row[_MIN_COL])
        if minutes is None:
            continue
        rec = {"team": idx[ti], "player": idx[pi], "minutes": minutes}
        for field, col in _STAT_COLS.items():
            rec[field] = _to_int(row[col])
        out.append(rec)
    return out


_METRIC_FIELDS = list(_STAT_COLS.keys())


def link_fixtures(
    session: Session,
    competition,
    season: str,
    schedule_df: pd.DataFrame,
) -> dict:
    """Stamp `fixtures.fbref_match_id` from the FBref schedule.

    Matches each scheduled game to the existing fixture (created in Phase 3 from
    football-data.co.uk) on the natural key — competition, season, home, away —
    after reconciling FBref team names to canonical ids. Idempotent: re-running
    overwrites the same id. Returns counts; `unmatched` lists games with no
    fixture (e.g. ordering/postponement quirks) for honest surfacing.
    """
    linked = 0
    unmatched: list[str] = []
    for _, row in schedule_df.reset_index().iterrows():
        game_id = row.get("game_id")
        if game_id is None or pd.isna(game_id):
            continue
        home = resolve_fbref_team(session, row["home_team"])
        away = resolve_fbref_team(session, row["away_team"])
        fixture = session.scalar(
            select(Fixture).where(
                Fixture.competition_id == competition.id,
                Fixture.season == season,
                Fixture.home_team_id == home.id,
                Fixture.away_team_id == away.id,
            )
        )
        if fixture is None:
            unmatched.append(str(row.get("game")))
            continue
        fixture.fbref_match_id = str(game_id)
        linked += 1
    return {"linked": linked, "unmatched": unmatched}


def _upsert_player(
    session: Session, fbref_id: str, name: str, team_id: int
) -> int:
    """Get-or-create a player by stable fbref_id; return its canonical id.

    Keyed on fbref_id (unique), so the same player always maps to one row even
    if the display name varies; current_team_id tracks the latest seen club.
    """
    return session.execute(
        insert(Player)
        .values(canonical_name=name, fbref_id=fbref_id, current_team_id=team_id)
        .on_conflict_do_update(
            index_elements=[Player.fbref_id],
            set_={"canonical_name": name, "current_team_id": team_id},
        )
        .returning(Player.id)
    ).scalar_one()


def ingest_match(
    session: Session,
    fixture: Fixture,
    competition_type: str,
    summary_df: pd.DataFrame,
    player_ids: dict[str, str],
) -> int:
    """Upsert every appearance in one match into `player_match` (+ `players`).

    One row per player (on their own side). Idempotent on (fixture_id, player_id);
    callers commit per match so a re-run or resume never duplicates. Raises
    UnknownTeamError / PlayerIdError on any reconciliation gap — fail loud.
    Returns the number of appearances upserted.
    """
    n = 0
    for rec in summary_to_player_stats(summary_df):
        team = resolve_fbref_team(session, rec["team"])
        if team.id == fixture.home_team_id:
            team_id, opponent_id, is_home = (
                fixture.home_team_id, fixture.away_team_id, True,
            )
        elif team.id == fixture.away_team_id:
            team_id, opponent_id, is_home = (
                fixture.away_team_id, fixture.home_team_id, False,
            )
        else:
            raise UnknownTeamError(
                f"{rec['team']!r} resolved to team {team.id}, which is neither "
                f"side of fixture {fixture.id} ({fixture.fbref_match_id})."
            )

        fbref_id = player_ids.get(rec["player"])
        if not fbref_id:
            raise PlayerIdError(
                f"no FBref id for {rec['player']!r} in match "
                f"{fixture.fbref_match_id} — HTML/df join gap."
            )
        player_id = _upsert_player(session, fbref_id, rec["player"], team_id)

        vals = {
            "fixture_id": fixture.id,
            "competition_id": fixture.competition_id,
            "competition_type": competition_type,
            "season": fixture.season,
            "date": fixture.date,
            "player_id": player_id,
            "team_id": team_id,
            "opponent_id": opponent_id,
            "is_home": is_home,
            "minutes": rec["minutes"],
            **{f: rec[f] for f in _METRIC_FIELDS},
        }
        session.execute(
            insert(PlayerMatch)
            .values(**vals)
            .on_conflict_do_update(
                constraint="uq_player_match",
                set_={k: vals[k] for k in (["date", "minutes"] + _METRIC_FIELDS)},
            )
        )
        n += 1
    return n


def parse_player_ids(match_html: str) -> dict[str, str]:
    """Map ``display name -> FBref player id`` for both lineups in a match page.

    Scoped to the two ``stats_<squad>_summary`` tables, so it excludes the page's
    comparison widgets and "related players" links (a blanket href scrape would
    pull in ~100 unrelated ids). The id comes from each player row's
    ``data-append-csv`` attribute; the name is the cell's anchor text, which
    matches the soccerdata DataFrame's player index exactly.
    """
    doc = lxml_html.fromstring(match_html)
    out: dict[str, str] = {}
    cells = doc.xpath(
        '//table[contains(@id, "_summary")]'
        '//tbody//th[@data-stat="player" and @data-append-csv]'
    )
    for cell in cells:
        fbref_id = cell.get("data-append-csv")
        name = cell.text_content().strip()
        if fbref_id and name:
            out[name] = fbref_id
    return out


def _pending_fixtures(session: Session, competition: Competition, season: str):
    """Finished, FBref-linked fixtures with no player_match rows yet (resumable)."""
    rows = session.execute(
        select(Fixture.id, Fixture.fbref_match_id)
        .where(
            Fixture.competition_id == competition.id,
            Fixture.season == season,
            Fixture.status == "finished",
            Fixture.fbref_match_id.is_not(None),
        )
        .order_by(Fixture.date)
    ).all()
    pending = []
    for fixture_id, game_id in rows:
        already = session.scalar(
            select(PlayerMatch.id).where(PlayerMatch.fixture_id == fixture_id).limit(1)
        )
        if already is None:
            pending.append((fixture_id, game_id))
    return pending


def backfill_season(
    season: str = "2526",
    *,
    league: str = "ENG-Premier League",
    competition_name: str = "Premier League",
    limit: int | None = None,
    log=print,
) -> dict:
    """Backfill player_match for one league-season from FBref.

    Uses ONE persistent headful soccerdata session (Cloudflare is solved once,
    then ~14s per fresh match). Links the schedule to fixtures, then ingests each
    pending match, committing per match so the run is interruptible/resumable.

    REQUIRES THE VPN OFF — Cloudflare blocks the VPN exit IP and the session hangs.
    `limit` ingests only the first N pending matches (bounded live smoke test).
    """
    import soccerdata as sd

    available = sd.FBref.available_leagues()
    if league not in available:
        raise ValueError(
            f"league {league!r} not available to the FBref reader; "
            f"available: {available}"
        )

    with SessionLocal() as session:
        competition = session.scalar(
            select(Competition).where(Competition.name == competition_name)
        )
        if competition is None:
            raise ValueError(f"competition {competition_name!r} not seeded")

        fb = sd.FBref(leagues=league, seasons=[season], headless=False)
        log(f"[{season}] fetching schedule (solves Cloudflare once)…")
        schedule = fb.read_schedule()
        link = link_fixtures(session, competition, season, schedule)
        session.commit()
        log(f"[{season}] linked {link['linked']} fixtures, "
            f"{len(link['unmatched'])} unmatched")

        pending = _pending_fixtures(session, competition, season)
        if limit is not None:
            pending = pending[:limit]
        log(f"[{season}] {len(pending)} matches to ingest")

        ingested = 0
        skipped: list[str] = []
        for i, (fixture_id, game_id) in enumerate(pending, 1):
            t0 = time.time()
            try:
                df = fb.read_player_match_stats(stat_type="summary", match_id=game_id)
                html = (_FBREF_CACHE / f"match_{game_id}.html").read_text(
                    encoding="utf-8"
                )
                player_ids = parse_player_ids(html)
                fixture = session.get(Fixture, fixture_id)
                n = ingest_match(session, fixture, competition.type, df, player_ids)
                session.commit()
                ingested += 1
                log(f"  [{i}/{len(pending)}] {game_id}: {n} players "
                    f"({time.time() - t0:.0f}s)")
            except Exception as exc:  # surface + skip one match, keep the run alive
                session.rollback()
                msg = f"{game_id}: {type(exc).__name__}: {exc}"
                skipped.append(msg)
                log(f"  [{i}/{len(pending)}] SKIP {msg}")

        return {
            "competition": competition_name,
            "season": season,
            "linked": link["linked"],
            "pending": len(pending),
            "ingested": ingested,
            "skipped": skipped,
        }


# Operator-facing: competitions this backfill can ingest -> soccerdata league id.
# Both already have canonical teams + fixtures from football-data.co.uk, so they
# reuse the existing reconciliation/linking path (no fixture-sourcing needed).
LEAGUE_IDS = {
    "Premier League": "ENG-Premier League",
    "Championship": "ENG-Championship",
}


if __name__ == "__main__":
    import sys

    season = sys.argv[1] if len(sys.argv) > 1 else "2526"
    competition = sys.argv[2] if len(sys.argv) > 2 else "Premier League"
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else None
    if competition not in LEAGUE_IDS:
        raise SystemExit(
            f"unknown competition {competition!r}; choose from {list(LEAGUE_IDS)}"
        )
    report = backfill_season(
        season,
        league=LEAGUE_IDS[competition],
        competition_name=competition,
        limit=limit,
    )
    print(
        f"\n{report['competition']} {report['season']}: linked={report['linked']} "
        f"pending={report['pending']} ingested={report['ingested']} "
        f"skipped={len(report['skipped'])}"
    )
    for s in report["skipped"][:20]:
        print("  skip:", s)
