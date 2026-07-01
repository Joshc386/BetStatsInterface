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

import datetime as dt
import re
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
from ingestion.names import clean_name, normalise_for_match

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
    # Championship (FBref full club names -> football-data short names). Derived by
    # diffing the cached 2025-26 schedule vs teams.canonical_name; earlier seasons
    # (promotion/relegation) add their own clubs before that season's backfill.
    "Birmingham City": "Birmingham",
    "Charlton Athletic": "Charlton",
    "Coventry City": "Coventry",
    "Derby County": "Derby",
    "Hull City": "Hull",
    "Norwich City": "Norwich",
    "Oxford United": "Oxford",
    "Stoke City": "Stoke",
    "Swansea City": "Swansea",
    # added for 2023-24 / 2024-25 Championship (relegated/promoted clubs)
    "Cardiff City": "Cardiff",
    "Plymouth Argyle": "Plymouth",
    # Championship player-match-df FULL names. The SCHEDULE page spells these short
    # (QPR, Blackburn, West Brom, Preston, Sheffield Weds, Huddersfield, Rotherham),
    # so the schedule alias-diff resolved them — but the per-match player df uses the
    # full club name, which fail-loud skipped every fixture involving them. (The
    # two-spelling trap: cover BOTH the schedule and player-df spellings.)
    "Blackburn Rovers": "Blackburn",
    "Huddersfield Town": "Huddersfield",
    "Preston North End": "Preston",
    "Queens Park Rangers": "QPR",
    "Rotherham United": "Rotherham",
    "Sheffield Wednesday": "Sheffield Weds",
    "West Bromwich Albion": "West Brom",
    # Domestic-cup (ADR 0008) lower-league opponents drawn against covered clubs.
    # The FA Cup schedule/caption spells them short while the per-match player df
    # spells them in full (the two-spelling trap) — alias BOTH to the existing
    # football-data canonical, else the tie fail-loud skips. Found by the FA Cup
    # 2024-25 backfill (7 covered ties dropped).
    "Peterborough": "Peterboro",  # schedule/caption (canonical is fd.co.uk 'Peterboro')
    "Peterborough United": "Peterboro",  # player-df full name
    "Accrington Stanley": "Accrington",  # player-df (caption 'Accrington' already matches)
    "Doncaster Rovers": "Doncaster",  # player-df
    "Wycombe Wanderers": "Wycombe",  # player-df
    # Dagenham & Redbridge are non-league (outside the 4 tiers) — no canonical row,
    # so the caption 'Dag & Red' auto-creates it (ADR 0007); alias the player-df
    # full name onto that created spelling so ingest resolves to the same row.
    "Dagenham & Redbridge": "Dag & Red",
}


def canonical_team_name(fbref_name: str) -> str:
    """Map an FBref team name to its canonical (football-data) spelling.

    Applies the explicit alias when one exists, otherwise returns the cleaned
    name unchanged (most clubs share the same spelling across sources).
    """
    name = clean_name(fbref_name)
    return FBREF_TEAM_ALIASES.get(name, name)


def match_existing_team(session: Session, fbref_name: str) -> Team | None:
    """Find the canonical Team for an FBref name deterministically, or None.

    Resolution is explicit-alias / exact-canonical first, then a normalised-name
    match (accent/case/FC-fold) against `canonical_name` OR `fdcouk_name`. Never
    fuzzy. Returns None when no team matches (a club outside our universe, e.g. a
    European-cup opponent) or when the normalised key is ambiguous (>1 team) —
    the caller decides whether a None is fatal (ingest) or expected (backfill).
    """
    canonical = canonical_team_name(fbref_name)
    team = session.scalar(select(Team).where(Team.canonical_name == canonical))
    if team is not None:
        return team
    key = normalise_for_match(fbref_name)
    if not key:
        return None
    matches: dict[int, Team] = {}
    for t in session.scalars(select(Team)):
        candidates = [t.canonical_name]
        if t.fdcouk_name:
            candidates.append(t.fdcouk_name)
        if any(normalise_for_match(c) == key for c in candidates):
            matches[t.id] = t
    if len(matches) == 1:
        return next(iter(matches.values()))
    return None


def resolve_fbref_team(
    session: Session, fbref_name: str, fbref_id: str | None = None
) -> Team:
    """Return the canonical Team for an FBref team, id-first then name/alias.

    When `fbref_id` is known (parsed from the match page), resolve by it first —
    robust to spelling drift, mirroring how players key on a stable id. On a miss
    (id not yet backfilled), fall back to deterministic name matching and, if a
    row is found, **attach** the fbref_id to it (the cross-source seam link) so
    later lookups are id-first.

    Raises UnknownTeamError if nothing matches — we never create teams from FBref
    in the league path (the canonical universe comes from football-data.co.uk); a
    miss means a missing alias, which must be fixed, not silently papered over.
    """
    if fbref_id:
        team = session.scalar(select(Team).where(Team.fbref_id == fbref_id))
        if team is not None:
            return team

    team = match_existing_team(session, fbref_name)
    if team is None:
        raise UnknownTeamError(
            f"FBref team {fbref_name!r} -> {canonical_team_name(fbref_name)!r} not "
            "found in teams; add an entry to FBREF_TEAM_ALIASES."
        )
    if fbref_id and not team.fbref_id:
        team.fbref_id = fbref_id  # attach the seam handle for next time
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


def _schedule_date(row) -> dt.datetime:
    """The schedule row's kickoff date as a tz-aware datetime (UTC midnight).

    The play-off page gives a date but no canonical time; date-level precision is
    all the rolling-window queries need.
    """
    ts = pd.Timestamp(row.get("date"))
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.to_pydatetime()


def _is_playoff_round(row) -> bool:
    """True for EFL promotion play-off rows (FBref `round` = 'Promotion play-offs
    — …'); False for the regular season (`round` = the competition name)."""
    return "play-off" in str(row.get("round") or "").lower()


def link_fixtures(
    session: Session,
    competition,
    season: str,
    schedule_df: pd.DataFrame,
    playoff_competition=None,
) -> dict:
    """Stamp `fixtures.fbref_match_id` from the FBref schedule.

    Regular-season games match the existing fixture (created in Phase 3 from
    football-data.co.uk) on the natural key — competition, season, home, away —
    after reconciling FBref team names to canonical ids.

    Promotion play-off games share that orientation with the league meeting, so
    they MUST NOT reuse the league fixture (it would overwrite league player data
    with play-off data and mis-tag it `club_league`). When `playoff_competition`
    is given, each play-off game gets its own fixture under that competition; the
    differing `competition_id` keeps the natural key from colliding. Without it
    (e.g. the Premier League, which has no play-offs) such rows are surfaced as
    `unmatched`.

    Idempotent: re-running overwrites the same ids. Returns counts; `unmatched`
    lists games with no fixture (ordering/postponement quirks) for honest
    surfacing.
    """
    linked = 0
    playoff_linked = 0
    unmatched: list[str] = []
    for _, row in schedule_df.reset_index().iterrows():
        game_id = row.get("game_id")
        if game_id is None or pd.isna(game_id):
            continue
        home = resolve_fbref_team(session, row["home_team"])
        away = resolve_fbref_team(session, row["away_team"])

        if _is_playoff_round(row):
            if playoff_competition is None:
                unmatched.append(str(row.get("game")))
                continue
            _link_playoff_fixture(
                session, playoff_competition, season, home, away,
                _schedule_date(row), str(game_id),
            )
            playoff_linked += 1
            continue

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
    return {"linked": linked, "playoff_linked": playoff_linked, "unmatched": unmatched}


def _link_playoff_fixture(
    session: Session, playoff_competition, season: str, home, away,
    date: dt.datetime, game_id: str,
) -> Fixture:
    """Get-or-create the play-off fixture under its own competition, idempotently.

    football-data.co.uk does not cover the play-offs, so unlike league fixtures
    there is no pre-existing row — we create it here (player data only)."""
    fixture = session.scalar(
        select(Fixture).where(
            Fixture.competition_id == playoff_competition.id,
            Fixture.season == season,
            Fixture.home_team_id == home.id,
            Fixture.away_team_id == away.id,
        )
    )
    if fixture is None:
        fixture = Fixture(
            competition_id=playoff_competition.id,
            season=season,
            date=date,
            home_team_id=home.id,
            away_team_id=away.id,
            status="finished",
            fbref_match_id=game_id,
        )
        session.add(fixture)
        session.flush()
    else:
        fixture.fbref_match_id = game_id
        fixture.date = date
    return fixture


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


_TEAM_TABLE_ID = re.compile(r"^stats_([0-9a-f]+)_summary$")
_CAPTION_SUFFIX = " Player Stats Table"


def parse_team_ids(match_html: str) -> dict[str, str]:
    """Map ``team display name -> FBref team id`` for both squads in a match page.

    Each squad's summary table carries the team's FBref id in its element id
    (``stats_<fbref_id>_summary``) and the team's name in its ``<caption>``
    (``"<Team> Player Stats Table"``). Pairing the two recovers the team id with
    no network call — the ids are already in the cached HTML. The caption uses
    FBref's *schedule* short spelling (e.g. "Manchester Utd"), so callers
    reconcile it through the same alias/normalisation path as other FBref names.
    """
    doc = lxml_html.fromstring(match_html)
    out: dict[str, str] = {}
    for table in doc.xpath('//table[contains(@id, "_summary")]'):
        m = _TEAM_TABLE_ID.match(table.get("id") or "")
        if not m:
            continue
        captions = table.xpath("./caption")
        if not captions:
            continue
        name = captions[0].text_content().strip()
        if name.endswith(_CAPTION_SUFFIX):
            name = name[: -len(_CAPTION_SUFFIX)].strip()
        if name:
            out[name] = m.group(1)
    return out


def _pending_fixtures(
    session: Session, competition_ids: list[int], season: str
):
    """Finished, FBref-linked fixtures with no player_match rows yet (resumable).

    Spans every competition in `competition_ids` so one backfill covers both the
    league and its play-offs in a single pass.
    """
    rows = session.execute(
        select(Fixture.id, Fixture.fbref_match_id)
        .where(
            Fixture.competition_id.in_(competition_ids),
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

        # The play-offs (if any for this league) are sourced from the SAME FBref
        # schedule but routed to their own competition so they never contaminate
        # league form. Named "<league> Play-offs"; absent for e.g. the PL.
        playoff = session.scalar(
            select(Competition).where(
                Competition.name == f"{competition_name} Play-offs"
            )
        )
        ctype_by_comp = {competition.id: competition.type}
        comp_ids = [competition.id]
        if playoff is not None:
            ctype_by_comp[playoff.id] = playoff.type
            comp_ids.append(playoff.id)

        fb = sd.FBref(leagues=league, seasons=[season], headless=False)
        log(f"[{season}] fetching schedule (solves Cloudflare once)…")
        schedule = fb.read_schedule()
        link = link_fixtures(session, competition, season, schedule, playoff)
        session.commit()
        log(f"[{season}] linked {link['linked']} league + "
            f"{link['playoff_linked']} play-off fixtures, "
            f"{len(link['unmatched'])} unmatched")

        pending = _pending_fixtures(session, comp_ids, season)
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
                # Capture each squad's FBref id from the same cached page and
                # attach it to the canonical row (the seam link), so identity is
                # id-backed going forward — fail-loud on an unknown league club.
                for team_name, team_fbref_id in parse_team_ids(html).items():
                    resolve_fbref_team(session, team_name, fbref_id=team_fbref_id)
                fixture = session.get(Fixture, fixture_id)
                n = ingest_match(
                    session, fixture, ctype_by_comp[fixture.competition_id],
                    df, player_ids,
                )
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
            "playoff_linked": link["playoff_linked"],
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
