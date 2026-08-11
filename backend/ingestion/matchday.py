"""Supervised match-day player refresh (Phase 5, tier 2) — the FBref part.

FBref player ingestion is headful, VPN-off and Cloudflare-gated, so it can never
run unattended: you run this after match days with the machine awake. It wraps
the existing per-competition watchdog (``ingestion.run_backfill``) so ONE command
refreshes every competition that has finished-but-unfetched player data, runs the
zero-network cup/European team-row pass for any cup competition it touched, then
sweeps the ``uc_driver.exe`` orphans a clean backfill exit leaves behind (the
watchdog only sweeps on stall/restart; clean exits leak — 59 seen after a chain
day, each holding a Chrome window).

Default (no args): the two leagues (Premier League, Championship) for the current
season, and only those with pending work — the frequent case; play-offs fold in
automatically via the watchdog. After a cup or European round, name the
competitions explicitly:

    python -m ingestion.matchday "FA Cup" "Champions League"

Every run then reports any cup/European competition still holding unfetched
player data for the current season, with the command to fetch it. The
unattended task only ever ingests the two leagues, so without that line an
unfetched cup round leaves a log identical to a day with no work at all.

UEFA Super Cup is excluded (soccerdata's ``read_schedule`` crashes on its
single-match page — ADR 0011); requesting it fails loud.

Run (VPN OFF, machine awake):  python -m ingestion.matchday
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys

from ingestion import cups, run_backfill
from ingestion.upcoming import season_for

# Player competitions by ingestion path (see ingestion.run_backfill routing).
LEAGUE_PLAYER_COMPETITIONS = ["Premier League", "Championship"]  # players.py path
CUP_PLAYER_COMPETITIONS = [
    "FA Cup",
    "EFL Cup",
    "Champions League",
    "Europa League",
    "Conference League",
]  # cups.py path — UEFA Super Cup deferred (ADR 0011), so omitted
ALL_PLAYER_COMPETITIONS = LEAGUE_PLAYER_COMPETITIONS + CUP_PLAYER_COMPETITIONS


def plan_competitions(
    requested: list[str] | None, pending_leagues: set[str]
) -> list[str]:
    """Resolve the ordered competition run-list (pure — no DB, no network).

    ``requested`` None -> the leagues that have pending player work, in the fixed
    PL-then-Championship order. An explicit list is validated against the
    supported set (fail loud on a typo or the deferred Super Cup) and its order
    is preserved — a cup evening runs exactly what you name.
    """
    if requested is None:
        return [c for c in LEAGUE_PLAYER_COMPETITIONS if c in pending_leagues]
    unknown = [c for c in requested if c not in ALL_PLAYER_COMPETITIONS]
    if unknown:
        raise ValueError(
            f"unknown/unsupported player competition(s): {unknown}; "
            f"choose from {ALL_PLAYER_COMPETITIONS}"
        )
    return list(requested)


def _sweep_orphans(log=print) -> None:
    """Kill detached ``uc_driver.exe`` processes a clean backfill exit leaves
    behind (list-form args, no shell)."""
    subprocess.run(["taskkill", "/F", "/IM", "uc_driver.exe"], capture_output=True)
    log("[matchday] swept uc_driver.exe orphans")


def manual_pending(season: str) -> dict[str, int]:
    """Cup/European competitions holding finished-but-unfetched player data.

    The unattended task runs with no args, so it only ever ingests the two
    leagues — a cup round or European night needs an explicit request, and
    nothing else would say so. Pure DB reads (``_pending``), no network.

    Current season only, deliberately: a handful of older fixtures (minor-nation
    qualifiers, a couple of one-offs) have no player data on FBref at all, and
    reporting those forever would train the reader to skip the line.
    """
    return {
        comp: n
        for comp in CUP_PLAYER_COMPETITIONS
        if (n := run_backfill._pending(season, comp)) > 0
    }


def _log_manual_pending(season: str, log) -> dict[str, int]:
    """Report the work the unattended run will not do, with the command to do it."""
    pending = manual_pending(season)
    if pending:
        detail = ", ".join(f"{comp} {n}" for comp, n in pending.items())
        args = " ".join(f'"{comp}"' for comp in pending)
        log(f"[matchday] MANUAL RUN NEEDED — pending player data: {detail}")
        log(f"[matchday]   python -m ingestion.matchday {args}   (VPN OFF, headful)")
    return pending


def run_matchday(
    competitions: list[str] | None = None,
    season: str | None = None,
    now: dt.datetime | None = None,
    log=print,
) -> dict:
    """Refresh player data for the planned competitions under the watchdog.

    Each competition runs to completion (or the watchdog's give-up), then — for a
    cup/European competition — its team_match rows are built from the freshly
    cached pages (zero network), and orphaned drivers are swept before the next.
    """
    season = season or season_for(now or dt.datetime.now(dt.timezone.utc))

    # Only the default (unnamed) run needs the pending-work probe; an explicit
    # request runs exactly what it names.
    pending_leagues = (
        {c for c in LEAGUE_PLAYER_COMPETITIONS if run_backfill._pending(season, c) > 0}
        if competitions is None
        else set()
    )
    plan = plan_competitions(competitions, pending_leagues)

    if not plan:
        log(f"[matchday] no pending league player data for {season}")
        # The quiet-day case this matters most for — an unfetched cup round
        # would otherwise leave a log identical to a day with genuinely no work.
        return {
            "season": season,
            "ran": [],
            "results": {},
            "pending_manual": _log_manual_pending(season, log),
        }

    log(f"[matchday] {season}: refreshing {plan}")
    results: dict[str, int] = {}
    for comp in plan:
        log(f"[matchday] --- {comp} {season} ---")
        code = run_backfill.run(season, comp)
        results[comp] = code
        if comp in CUP_PLAYER_COMPETITIONS:
            # Player rows just landed in cache; build the two team rows/fixture.
            log(f"[matchday] building {comp} team_match rows (zero network)")
            cups.backfill_cup_team_match(season, cup_name=comp, log=log)
        _sweep_orphans(log)

    log(f"[matchday] done — {results}")
    # After the run, so anything just ingested is no longer counted as pending.
    return {
        "season": season,
        "ran": plan,
        "results": results,
        "pending_manual": _log_manual_pending(season, log),
    }


if __name__ == "__main__":
    report = run_matchday(sys.argv[1:] or None)
    # Non-zero if any competition's watchdog gave up (stall / max restarts) so a
    # scripted run surfaces it; a clean "nothing to do" is success.
    sys.exit(1 if any(code != 0 for code in report["results"].values()) else 0)
