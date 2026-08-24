"""Knockout-competition player-match ingestion — domestic cups (ADR 0008) and
European club competitions (ADR 0011).

These competitions have no football-data.co.uk fixtures to match against, so
unlike the league path this *creates* fixtures from the FBref schedule, scoped
to Covered ties: at least one covered (Premier League / Championship) club that
season. Unknown opponents (random-draw lower-/non-league clubs; foreign clubs
in Europe) are auto-created and logged, per ADR 0007 — EXCEPT when the name
looks like an existing club under a different spelling, which fails loud
instead (14 in-universe clubs were silently duplicated this way before
2026-07-03; a dropped tie is recoverable via alias + re-run, a duplicate row
silently splits a club's history). Both squads come along via the
competition-agnostic ``ingest_match``.

European fixtures carry ``stage`` (the schedule round) in the natural key —
the same pairing can recur at the same venue within one European season
(league phase + knockout leg; pre-2024 group + knockout rematches), which
domestic knockouts structurally cannot (ADR 0011, migration 0005).

Live fetches require the VPN OFF and ride the watchdog supervisor (one-shot
fetches hang on Cloudflare). See memory: cups-internationals-sourcing.
"""

from __future__ import annotations

import datetime as dt
import re

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.facts import Fixture, PlayerMatch, TeamMatch
from app.models.reference import Competition, Team
from ingestion.names import (
    GENERIC_NAME_TOKENS,
    clean_name,
    guard_token,
    normalise_for_match,
)
from ingestion.players import (
    _FBREF_CACHE,
    ScorelineError,
    UnknownTeamError,
    _schedule_date,
    ingest_match,
    match_existing_team,
    parse_corners,
    parse_player_ids,
    parse_scoreline,
    parse_team_ids,
)

# A cup tie is "covered" when a side plays in one of these that season
# (season-aware, so a relegated club stops dragging its cup ties in once it
# leaves the tracked top two).
COVERED_COMPETITIONS = ("Premier League", "Championship")


def covered_fbref_ids(session: Session, season: str) -> set[str]:
    """The covered set as fbref_ids — identity rather than spelling.

    ``covered_team_ids`` answers "which clubs are in scope"; this answers it in
    the only currency the match page speaks. Needed because a cup SCHEDULE
    carries names only, so a non-league club can read as a covered one.
    """
    team_ids = covered_team_ids(session, season)
    return {
        t.fbref_id
        for t in session.scalars(select(Team).where(Team.id.in_(team_ids)))
        if t.fbref_id
    }


def tie_is_covered(
    session: Session, fbref_ids: tuple[str | None, str | None], season: str
) -> bool:
    """Is this tie genuinely ours, judged on the match page's own team ids?

    ``select_covered_games`` can only match the schedule's NAMES, and names
    collide: FBref's FA Cup schedule lists the non-league Bournemouth FC and AFC
    Liverpool as "Bournemouth" and "Liverpool", which read exactly like the two
    Premier League clubs. Re-checking once the match page has given us real ids
    drops those ties before anything is written. Quiet by design — a collision
    is not our tie at all, so alarming on it would fire every qualifying round.
    """
    covered = covered_fbref_ids(session, season)
    return any(fid in covered for fid in fbref_ids if fid)


def _team_match_clubs(session: Session, season: str, competition: str) -> set[int]:
    """Clubs with a team_match row in this competition-season (football-data.co.uk)."""
    return set(
        session.scalars(
            select(TeamMatch.team_id)
            .join(Competition, Competition.id == TeamMatch.competition_id)
            .where(TeamMatch.season == season, Competition.name == competition)
        )
    )


def _fixture_clubs(session: Session, season: str, competition: str) -> set[int]:
    """Clubs appearing in this competition-season's fixtures (ESPN + ingest)."""
    rows = session.execute(
        select(Fixture.home_team_id, Fixture.away_team_id)
        .join(Competition, Competition.id == Fixture.competition_id)
        .where(Fixture.season == season, Competition.name == competition)
    ).all()
    return {team_id for row in rows for team_id in row}


def covered_divergence(session: Session, season: str) -> dict[str, tuple[int, int]]:
    """Competitions whose two covered-set sources disagree -> (team_match, fixtures).

    Disagreement is the signature of a source outage, so it is reported rather
    than absorbed. Empty dict when both sources agree everywhere.
    """
    divergent = {}
    for competition in COVERED_COMPETITIONS:
        from_team_match = _team_match_clubs(session, season, competition)
        from_fixtures = _fixture_clubs(session, season, competition)
        if from_team_match != from_fixtures:
            divergent[competition] = (len(from_team_match), len(from_fixtures))
    return divergent


def covered_team_ids(session: Session, season: str, *, log=None) -> set[int]:
    """Team ids playing Premier League / Championship football this season.

    These are the clubs whose cup ties are in scope; either side being covered
    pulls the tie in (and the opponent's players come along as a coverage bonus).

    Sourced from the UNION of team_match and fixtures rows (ADR 0012). team_match
    alone means football-data.co.uk alone, and an outage there silently empties
    the covered set — ruling real ties out of scope with nothing logged (observed
    2026-08: with E0 unpublished, no Premier League club was covered for 2627,
    while the ESPN-sourced fixtures held all 20). Historically the union is a
    no-op: wherever team_match rows exist, fixtures do too. Pass ``log`` to have
    a divergence between the two sources reported.
    """
    covered: set[int] = set()
    for competition in COVERED_COMPETITIONS:
        covered |= _team_match_clubs(session, season, competition)
        covered |= _fixture_clubs(session, season, competition)
    if log is not None:
        for competition, (n_tm, n_fx) in covered_divergence(session, season).items():
            log(
                f"[covered] {competition} {season}: {n_tm} clubs from team_match "
                f"vs {n_fx} from fixtures - source divergence"
            )
    return covered


# Names the cup path may auto-create even though an existing team shares their
# first word — i.e. confirmed genuinely-distinct clubs (a non-league "Oxford City"
# vs the league "Oxford"). Empty until such a club actually appears in a draw.
CUP_CREATE_ALLOWLIST: frozenset[str] = frozenset({
    # OFC qualifiers (2026-07-20): a nation, not New Zealand
    "New Caledonia",
    # CAF qualifiers (2026-07-20): Congo (Brazzaville), not Congo DR
    "Congo",
    # CAF qualifiers (2026-07-20): a nation, not South Africa
    "South Sudan",
    # AFC qualifiers (2026-07-20): North Korea, not Korea Republic
    "Korea DPR",
})

# Generic club-name prefixes skipped when picking the guard token below: at
# European volume, first-word matching alone would deadlock on unrelated clubs
# ("FC Porto" vs "FC Copenhagen"). The guard compares the first NON-generic
# token instead — which also sharpens it domestically ("AFC Wimbledon" guards
# on "wimbledon", not "afc").
# Moved to ingestion/names.py so the football-data.co.uk resolver shares one
# implementation (see guard_token's docstring). Re-exported: this module's
# comments and callers have always referred to _guard_token.
_guard_token = guard_token


# FBref schedule team names in European competitions carry a country code
# adjacent to the name ("eng Arsenal" / "Bayern Munich de"). Split it off for
# matching, and map it to the country stored on auto-created teams. Codes are
# FBref's own; the map fills out as competitions are ingested (unknown codes
# log + leave country NULL — re-runs heal it once mapped).
FBREF_COUNTRY_CODES = {
    "eng": "England", "sct": "Scotland", "wls": "Wales", "nir": "Northern Ireland",
    "ie": "Ireland", "es": "Spain", "de": "Germany", "it": "Italy", "fr": "France",
    "pt": "Portugal", "nl": "Netherlands", "be": "Belgium", "at": "Austria",
    "ch": "Switzerland", "tr": "Turkey", "gr": "Greece", "ua": "Ukraine",
    "cz": "Czechia", "hr": "Croatia", "rs": "Serbia", "dk": "Denmark",
    "se": "Sweden", "no": "Norway", "pl": "Poland", "ro": "Romania",
    "hu": "Hungary", "sk": "Slovakia", "si": "Slovenia", "bg": "Bulgaria",
    "cy": "Cyprus", "il": "Israel", "az": "Azerbaijan", "kz": "Kazakhstan",
    "by": "Belarus", "md": "Moldova", "mk": "North Macedonia", "al": "Albania",
    "ba": "Bosnia and Herzegovina", "me": "Montenegro", "xk": "Kosovo",
    "lv": "Latvia", "lt": "Lithuania", "ee": "Estonia", "fi": "Finland",
    "is": "Iceland", "fo": "Faroe Islands", "mt": "Malta", "lu": "Luxembourg",
    "am": "Armenia", "ge": "Georgia", "ad": "Andorra", "sm": "San Marino",
    "gi": "Gibraltar", "li": "Liechtenstein", "ru": "Russia",
}

_COUNTRY_CODE_RE = re.compile(r"^(?:([a-z]{2,3})\s+)?(.*?)(?:\s+([a-z]{2,3}))?$")


def split_country_code(raw_name: str) -> tuple[str, str | None]:
    """Split an FBref schedule team name from its adjacent country code.

    Returns ``(name, code)`` — code None when no lowercase 2-3 letter token
    leads or trails the name (domestic schedules carry none, so this is a
    no-op for cups). Only fully-lowercase tokens qualify, so club words
    ("St", "Port") are never eaten.
    """
    m = _COUNTRY_CODE_RE.match(raw_name.strip())
    if m is None:  # pragma: no cover - the pattern matches any string
        return raw_name.strip(), None
    lead, name, trail = m.groups()
    return name.strip(), lead or trail


def resolve_or_create_fbref_team(
    session: Session, fbref_name: str, fbref_id: str, country: str | None = None
) -> tuple[Team, bool]:
    """Resolve an FBref cup/European team to a canonical row, creating if new.

    Id-first, then deterministic name/alias match (`match_existing_team`) — but
    ONLY onto a club whose own fbref_id is unset or identical. A stored club
    carrying a different id is a different club however its name reads, and
    returning it is silent corruption (FA Cup 2627: "AFC Liverpool" onto
    Liverpool). On an accepted name match the fbref_id is attached (the seam
    link) so no duplicate is made.
    Only when nothing matches is a new row created (auto-create + log, ADR 0008) —
    this is the one place this path diverges from the league fail-loud rule.
    Guard: creation is refused when an existing team shares the name's first
    non-generic token (`_guard_token`), the signature of an in-universe club
    under a different FBref spelling ("Bradford City" vs canonical "Bradford") —
    that needs an alias, not a new row. `country` (from the schedule's country
    code, ADR 0011) is stored on creation and back-filled on a NULL-country
    match, so re-runs heal rows created before the code was mapped.
    Returns ``(team, created)``.
    """
    by_id = session.scalar(select(Team).where(Team.fbref_id == fbref_id))
    if by_id is not None:
        if country and not by_id.country:
            by_id.country = country
        return by_id, False

    existing = match_existing_team(session, fbref_name)
    if existing is not None and existing.fbref_id in (None, "", fbref_id):
        if not existing.fbref_id:
            existing.fbref_id = fbref_id  # attach the seam handle
        if country and not existing.country:
            existing.country = country
        return existing, False
    # A name match whose fbref_id CONTRADICTS the page is a different club, and
    # returning it silently is how AFC Liverpool's cup tie was once recorded
    # against Liverpool. Fall through: either this is a genuinely new club, or
    # the guard token below stops and asks for a human decision.

    name = clean_name(fbref_name)
    if name not in CUP_CREATE_ALLOWLIST:
        token = _guard_token(name)
        suspects = sorted(
            t.canonical_name
            for t in session.scalars(select(Team))
            if any(
                _guard_token(n) == token
                for n in (t.canonical_name, t.fdcouk_name)
                if n
            )
        )
        if suspects:
            raise UnknownTeamError(
                f"refusing to auto-create team {name!r}: existing team(s) "
                f"{suspects} share its guard token {token!r} and may be the same "
                "club. Same club -> add a FBREF_TEAM_ALIASES entry; genuinely "
                "distinct -> add it to CUP_CREATE_ALLOWLIST."
            )

    team = Team(canonical_name=name, fbref_id=fbref_id, country=country)
    session.add(team)
    session.flush()
    return team, True


def select_covered_games(
    session: Session, schedule_df: pd.DataFrame, season: str
) -> list[dict]:
    """Filter an FBref cup schedule to the played ties in scope this season.

    A tie is in scope when at least one side is a covered (PL/Championship) club
    that season; rows with no `game_id` (not yet played) are dropped. Resolution
    is by name against existing teams — enough to detect a covered side; the
    opponent's id is recovered later from the match page. Returns lightweight
    dicts (no DB writes here): the schedule carries names + game_id only.
    """
    covered = covered_team_ids(session, season)
    games: list[dict] = []
    for _, row in schedule_df.reset_index().iterrows():
        game_id = row.get("game_id")
        if game_id is None or pd.isna(game_id):
            continue
        # European schedule names carry adjacent country codes; domestic don't
        # (split is a no-op there). Match on the cleaned name.
        home_name, home_code = split_country_code(str(row["home_team"]))
        away_name, away_code = split_country_code(str(row["away_team"]))
        home = match_existing_team(session, home_name)
        away = match_existing_team(session, away_name)
        if (home is not None and home.id in covered) or (
            away is not None and away.id in covered
        ):
            round_ = row.get("round")
            games.append(
                {
                    "game_id": str(game_id),
                    "date": _schedule_date(row),
                    "home_name": home_name,
                    "away_name": away_name,
                    "home_country": FBREF_COUNTRY_CODES.get(home_code or ""),
                    "away_country": FBREF_COUNTRY_CODES.get(away_code or ""),
                    # stage joins the fixture natural key (European rematches
                    # repeat an orientation within a season, ADR 0011)
                    "stage": "" if round_ is None or pd.isna(round_) else str(round_),
                }
            )
    return games


def pair_team_ids(
    home_name: str, away_name: str, caption_ids: dict[str, str]
) -> tuple[str | None, str | None]:
    """Pair schedule home/away names to the match page's caption-derived fbref_ids.

    Both come from FBref (schedule short names; caption short names), so a
    normalised-name match aligns them — giving each side its stable team id and,
    by position, the home/away orientation. Returns ``(home_id, away_id)`` with
    None for any side that does not pair (caller skips + logs that game).
    """
    by_norm = {normalise_for_match(c): cid for c, cid in caption_ids.items()}
    return by_norm.get(normalise_for_match(home_name)), by_norm.get(
        normalise_for_match(away_name)
    )


def get_or_create_cup_fixture(
    session: Session,
    competition: Competition,
    season: str,
    home: Team,
    away: Team,
    date: dt.datetime,
    game_id: str,
    stage: str = "",
) -> Fixture:
    """Get-or-create a knockout/European Fixture under its own competition,
    idempotently.

    football-data.co.uk does not cover these, so (unlike league fixtures) there
    is no pre-existing row — we create it here. The natural key is
    (competition_id, season, home, away, stage): domestic cups pass stage=''
    (their structure never repeats an orientation in a season — replays and
    two-legged ties swap venue); European competitions pass the schedule round,
    because the same orientation CAN recur across stages there (ADR 0011).
    """
    fixture = session.scalar(
        select(Fixture).where(
            Fixture.competition_id == competition.id,
            Fixture.season == season,
            Fixture.home_team_id == home.id,
            Fixture.away_team_id == away.id,
            Fixture.stage == stage,
        )
    )
    if fixture is None:
        fixture = Fixture(
            competition_id=competition.id,
            season=season,
            date=date,
            home_team_id=home.id,
            away_team_id=away.id,
            status="finished",
            stage=stage,
            fbref_match_id=game_id,
        )
        session.add(fixture)
        session.flush()
    else:
        fixture.fbref_match_id = game_id
        fixture.date = date
    return fixture


def _already_ingested(session: Session, game_id: str) -> bool:
    """True when this cup game's fixture already carries player_match rows.

    Lets a watchdog restart resume: an already-ingested tie is skipped before any
    network fetch, so restarts cost nothing on the games already done.
    """
    fixture_id = session.scalar(
        select(Fixture.id).where(Fixture.fbref_match_id == game_id)
    )
    if fixture_id is None:
        return False
    return (
        session.scalar(
            select(PlayerMatch.id)
            .where(PlayerMatch.fixture_id == fixture_id)
            .limit(1)
        )
        is not None
    )


def backfill_cup_season(
    season: str,
    *,
    cup_name: str,
    league: str,
    limit: int | None = None,
    log=print,
) -> dict:
    """Backfill player_match for one cup-season from FBref (covered ties only).

    ONE persistent headful session (Cloudflare solved once). Reads the cup
    schedule, selects covered ties, then per game: fetches the match page,
    recovers both squads' fbref_ids, resolves/creates teams + fixture, and reuses
    ingest_match. Commits per match (resumable). REQUIRES THE VPN OFF — and
    should run under the watchdog, as one-shot cup fetches hang on Cloudflare.
    """
    import soccerdata as sd

    available = sd.FBref.available_leagues()
    if league not in available:
        raise ValueError(
            f"league {league!r} not available to the FBref reader; run "
            f"ingestion.config_sync first. available: {available}"
        )

    with SessionLocal() as session:
        competition = session.scalar(
            select(Competition).where(Competition.name == cup_name)
        )
        if competition is None:
            raise ValueError(f"competition {cup_name!r} not seeded")

        fb = sd.FBref(leagues=league, seasons=[season], headless=False)
        log(f"[{cup_name} {season}] fetching schedule (solves Cloudflare once)…")
        schedule = fb.read_schedule()
        games = select_covered_games(session, schedule, season)
        log(f"[{cup_name} {season}] {len(games)} covered ties in schedule")

        # Phase 1: create fixtures + teams from each covered game's match page.
        created_fixtures = 0
        created_teams = 0
        uncovered = 0
        skipped: list[str] = []
        for game in games:
            game_id = game["game_id"]
            if _already_ingested(session, game_id):
                continue  # resume: skip games already done, no network fetch
            try:
                df = fb.read_player_match_stats(stat_type="summary", match_id=game_id)
                html = (_FBREF_CACHE / f"match_{game_id}.html").read_text(
                    encoding="utf-8"
                )
                caption_ids = parse_team_ids(html)
                home_id, away_id = pair_team_ids(
                    game["home_name"], game["away_name"], caption_ids
                )
                if not home_id or not away_id:
                    skipped.append(f"{game_id}: could not pair teams to ids")
                    continue
                if not tie_is_covered(session, (home_id, away_id), season):
                    # The schedule name said covered, the page's ids say
                    # otherwise — a collision, not our tie. Not a skip: nothing
                    # failed, and counting it would alarm every qualifying round.
                    uncovered += 1
                    continue
                home, h_new = resolve_or_create_fbref_team(
                    session, game["home_name"], home_id, game.get("home_country")
                )
                away, a_new = resolve_or_create_fbref_team(
                    session, game["away_name"], away_id, game.get("away_country")
                )
                created_teams += int(h_new) + int(a_new)
                if h_new:
                    log(f"  + created team {home.canonical_name!r} ({home_id})")
                if a_new:
                    log(f"  + created team {away.canonical_name!r} ({away_id})")
                # stage disambiguates only European fixtures; domestic cups keep
                # '' so their existing rows and re-run idempotency are untouched
                stage = game["stage"] if competition.type == "club_european" else ""
                fixture = get_or_create_cup_fixture(
                    session, competition, season, home, away, game["date"], game_id,
                    stage=stage,
                )
                # ingest immediately while the page df is in hand (resumable)
                n = ingest_match(
                    session, fixture, competition.type, df, parse_player_ids(html)
                )
                session.commit()
                created_fixtures += 1
                log(f"  [{game_id}] {n} players")
            except Exception as exc:  # surface + skip one game, keep the run alive
                session.rollback()
                skipped.append(f"{game_id}: {type(exc).__name__}: {exc}")
                log(f"  SKIP {game_id}: {type(exc).__name__}: {exc}")
            if limit is not None and created_fixtures >= limit:
                break

        if uncovered:
            log(
                f"[{cup_name} {season}] {uncovered} tie(s) dropped on identity: "
                "the schedule name read as a covered club, the match page's "
                "team ids disagreed"
            )
        return {
            "cup": cup_name,
            "season": season,
            "covered_ties": len(games),
            "fixtures": created_fixtures,
            "created_teams": created_teams,
            "uncovered": uncovered,
            "skipped": skipped,
        }


# team_match metric fields this cup path populates. xg is intentionally absent
# (deferred, stays NULL everywhere). corners comes from the page's
# team_stats_extra panel and is NULL only where that panel is absent upstream
# (a minority of FA Cup third-round pages); everything else is derivable from
# cached data.
_CUP_TEAM_METRICS = [
    "gf", "ga", "shots", "sot", "shots_conceded", "sot_conceded",
    "fouls", "yellows", "reds", "corners",
]


def exit_code(report: dict) -> int:
    """1 when a tie was skipped, so an automated run alarms (ADR 0012).

    This path swallows per-tie failures to keep a long backfill alive, and used
    to exit 0 regardless — fine when a human was reading the output, invisible
    once the run is unattended. A skip is a genuine failure (unresolvable team,
    pairing failure, parse error) and needs a human.

    Ties dropped on identity are NOT skips: nothing failed, the tie was simply
    never ours, and counting them would alarm on every qualifying round.
    Publication lag is not a skip either — select_covered_games drops schedule
    rows with no game_id, so an unpublished match is never attempted.
    """
    return 1 if report.get("skipped") else 0


def _int(value) -> int | None:
    """Coerce a SQL SUM (Decimal / None) to int or None."""
    return None if value is None else int(value)


def _team_stat_totals(session: Session, fixture_id: int) -> dict[int, dict]:
    """Sum a fixture's player_match rows per team → team-level event counts.

    SUM ignores NULLs, so a source-sparse match (whole match missing shots/fouls
    at player level) yields NULL totals — correctly left uncovered, not zeroed.
    """
    rows = session.execute(
        select(
            PlayerMatch.team_id,
            func.sum(PlayerMatch.shots),
            func.sum(PlayerMatch.sot),
            func.sum(PlayerMatch.fouls_committed),
            func.sum(PlayerMatch.yellows),
            func.sum(PlayerMatch.reds),
        )
        .where(PlayerMatch.fixture_id == fixture_id)
        .group_by(PlayerMatch.team_id)
    ).all()
    return {
        team_id: {
            "shots": _int(sh), "sot": _int(sot), "fouls": _int(fls),
            "yellows": _int(y), "reds": _int(r),
        }
        for team_id, sh, sot, fls, y, r in rows
    }


def backfill_cup_team_match(
    season: str, *, cup_name: str, log=print
) -> dict:
    """Build the two team_match rows per cup fixture from cached data — ZERO network.

    gf/ga come from the cached match scorebox (authoritative — includes own-goals a
    player-goal sum would miss); corners from the page's team_stats_extra panel
    (NULL where the panel is absent upstream — whole-match figures, ET included);
    shots/sot/fouls/yellows/reds by summing the fixture's already-ingested
    player_match rows; shots/sot_conceded from the opposite side. xg stays NULL
    (deferred). Idempotent on (fixture_id, team_id). A fixture with no cached
    page, no scorebox, or no player rows yet is skipped + logged (ADR 0008
    team-data follow-up). Works for any seeded club_cup / club_european
    competition whose fixtures carry an fbref_match_id.
    """
    with SessionLocal() as session:
        competition = session.scalar(
            select(Competition).where(Competition.name == cup_name)
        )
        if competition is None:
            raise ValueError(f"competition {cup_name!r} not seeded")

        fixtures = list(
            session.scalars(
                select(Fixture).where(
                    Fixture.competition_id == competition.id,
                    Fixture.season == season,
                    Fixture.fbref_match_id.is_not(None),
                )
            )
        )
        built = 0
        skipped: list[str] = []
        for fx in fixtures:
            game_id = fx.fbref_match_id
            try:
                html = (_FBREF_CACHE / f"match_{game_id}.html").read_text(
                    encoding="utf-8"
                )
                home_goals, away_goals = parse_scoreline(html)
            except (FileNotFoundError, ScorelineError) as exc:
                skipped.append(f"{game_id}: {type(exc).__name__}: {exc}")
                continue
            # None when the page lacks the team_stats_extra panel — corners
            # stays NULL but the team rows are still built.
            corners = parse_corners(html)
            home_corners, away_corners = corners if corners else (None, None)

            totals = _team_stat_totals(session, fx.id)
            if fx.home_team_id not in totals or fx.away_team_id not in totals:
                skipped.append(f"{game_id}: no player rows for both sides")
                continue
            home, away = totals[fx.home_team_id], totals[fx.away_team_id]

            for team_id, opp_id, is_home, gf, ga, crn, own, opp in (
                (fx.home_team_id, fx.away_team_id, True, home_goals, away_goals, home_corners, home, away),
                (fx.away_team_id, fx.home_team_id, False, away_goals, home_goals, away_corners, away, home),
            ):
                vals = {
                    "fixture_id": fx.id,
                    "competition_id": competition.id,
                    "competition_type": competition.type,
                    "season": season,
                    "date": fx.date,
                    "team_id": team_id,
                    "opponent_id": opp_id,
                    "is_home": is_home,
                    # Cup, European and international team rows are derived
                    # from cached FBref match pages (docs/adr/0015).
                    "source": "fbref",
                    "gf": gf,
                    "ga": ga,
                    "shots": own["shots"],
                    "sot": own["sot"],
                    "shots_conceded": opp["shots"],
                    "sot_conceded": opp["sot"],
                    "fouls": own["fouls"],
                    "yellows": own["yellows"],
                    "reds": own["reds"],
                    "corners": crn,
                }
                session.execute(
                    insert(TeamMatch)
                    .values(**vals)
                    .on_conflict_do_update(
                        constraint="uq_team_match",
                        set_={
                            k: vals[k]
                            for k in (["date", "source"] + _CUP_TEAM_METRICS)
                        },
                    )
                )
            session.commit()
            built += 1
            log(f"  [{game_id}] {home_goals}-{away_goals} team rows")

        return {
            "cup": cup_name, "season": season, "fixtures": len(fixtures),
            "built": built, "skipped": skipped,
        }


# Operator-facing: competitions this backfill can ingest -> soccerdata league.
# (run_backfill.py dispatches any of these to the supervised cup path.)
LEAGUE_IDS = {
    "FA Cup": "ENG-FA Cup",
    "EFL Cup": "ENG-EFL Cup",
    "Champions League": "EUR-Champions League",
    "Europa League": "EUR-Europa League",
    "Conference League": "EUR-Conference League",
    "UEFA Super Cup": "EUR-Super Cup",
}


if __name__ == "__main__":
    import sys

    # Two modes:
    #   player (default):  python -m ingestion.cups <season> <cup> [limit]   (needs VPN off, watchdog)
    #   team (zero-network): python -m ingestion.cups team <season> <cup>
    argv = sys.argv[1:]
    team_mode = bool(argv) and argv[0] == "team"
    if team_mode:
        argv = argv[1:]

    season = argv[0] if len(argv) > 0 else "2425"
    cup = argv[1] if len(argv) > 1 else "FA Cup"
    # Only the live player fetch needs a soccerdata league mapping; team mode is
    # zero-network and works for any seeded club_cup competition with cached
    # pages (incl. "Championship Play-offs").
    if not team_mode and cup not in LEAGUE_IDS:
        raise SystemExit(f"unknown cup {cup!r}; choose from {list(LEAGUE_IDS)}")

    if team_mode:
        report = backfill_cup_team_match(season, cup_name=cup)
        print(
            f"\n{report['cup']} {report['season']}: fixtures={report['fixtures']} "
            f"team_rows_built={report['built']} skipped={len(report['skipped'])}"
        )
    else:
        limit = int(argv[2]) if len(argv) > 2 else None
        report = backfill_cup_season(
            season, cup_name=cup, league=LEAGUE_IDS[cup], limit=limit
        )
        print(
            f"\n{report['cup']} {report['season']}: covered={report['covered_ties']} "
            f"fixtures={report['fixtures']} created_teams={report['created_teams']} "
            f"uncovered={report.get('uncovered', 0)} "
            f"skipped={len(report['skipped'])}"
        )
    for s in report["skipped"][:20]:
        print("  skip:", s)
    # Hand the failure up: the watchdog only sees an exit code, and matchday
    # only sees the watchdog's. Without this a skipped tie dies in the log.
    raise SystemExit(exit_code(report))
