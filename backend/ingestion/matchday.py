"""Supervised match-day player refresh (Phase 5, tier 2) — the FBref part.

FBref player ingestion is headful, VPN-off and Cloudflare-gated, so it can never
run unattended: you run this after match days with the machine awake. It wraps
the existing per-competition watchdog (``ingestion.run_backfill``) so ONE command
refreshes every competition that has finished-but-unfetched player data, runs the
zero-network cup/European team-row pass for any cup competition it touched, then
sweeps the ``uc_driver.exe`` orphans a clean backfill exit leaves behind (the
watchdog only sweeps on stall/restart; clean exits leak — 59 seen after a chain
day, each holding a Chrome window).

Default (no args): every competition with pending player work this season —
the two leagues, plus any cup or European round that has been played and not yet
fetched (ADR 0012). Play-offs fold in automatically via the watchdog. Naming
competitions explicitly still runs exactly those, in the order given:

    python -m ingestion.matchday "FA Cup" "Champions League"

Pending work is found two ways, because the two scopes differ. Domestic cups now
have ESPN-sourced fixture rows marked finished, so the same zero-network DB probe
the leagues use works on them. European ties are never written by ESPN, so they
are detected by asking ESPN directly whether a covered club played a tie we hold
no fixture for. Anything still pending AFTER the run is reported — that is a
genuine failure, not a quiet day.

UEFA Super Cup is excluded (soccerdata's ``read_schedule`` crashes on its
single-match page — ADR 0011); requesting it fails loud.

Run (VPN OFF, machine awake):  python -m ingestion.matchday
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys

from ingestion import coverage, cups, run_backfill, upcoming
from app.db import SessionLocal
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
    requested: list[str] | None,
    pending_leagues: set[str],
    pending_cups: set[str] = frozenset(),
) -> list[str]:
    """Resolve the ordered competition run-list (pure — no DB, no network).

    ``requested`` None -> everything with pending player work, in canonical order:
    the leagues first (PL then Championship, the frequent case), then any cup or
    European competition that has been played and not yet fetched. An explicit
    list is validated against the supported set (fail loud on a typo or the
    deferred Super Cup) and its order is preserved — a cup evening runs exactly
    what you name.
    """
    if requested is None:
        return [c for c in LEAGUE_PLAYER_COMPETITIONS if c in pending_leagues] + [
            c for c in CUP_PLAYER_COMPETITIONS if c in pending_cups
        ]
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


def cup_pending(season: str) -> dict[str, int]:
    """Cup/European competitions holding finished-but-unfetched player data.

    Pure DB reads (``_pending``), no network. This used to be dead weight: cup
    fixtures were created BY the ingest, so a tie never fetched had no row to
    count and this could not fire (it never once did). Domestic cup fixtures now
    arrive from ESPN already marked finished (ADR 0012), so the probe finally
    sees them — the same zero-network signal the leagues have always had.

    European ties are still not written by ESPN, so this only catches ones
    PARTIALLY ingested; ``espn_pending`` finds the rest.

    Current season only, deliberately: a handful of older fixtures (minor-nation
    qualifiers, a couple of one-offs) have no player data at all, and reporting
    those forever would train the reader to skip the line.
    """
    return {
        comp: n
        for comp in CUP_PLAYER_COMPETITIONS
        if (n := run_backfill._pending(season, comp)) > 0
    }


def espn_pending(season: str, log=print) -> dict[str, int]:
    """European competitions ESPN says have played, covered ties we do not hold.

    The half ``cup_pending`` structurally cannot see: with no fixture row there
    is nothing to probe. Costs one ESPN request per competition — no Cloudflare,
    nothing written, and an outage degrades to "no signal" rather than failing
    the run.
    """
    return upcoming.european_pending(season, log=log)


def pending_cup_competitions(season: str, log=print) -> dict[str, int]:
    """Every cup/European competition with player work outstanding, both sources."""
    pending = cup_pending(season)
    for comp, n in espn_pending(season, log=log).items():
        pending[comp] = max(pending.get(comp, 0), n)
    return pending


def _log_still_pending(season: str, log) -> dict[str, int]:
    """Report work still outstanding AFTER a run — a failure, not a quiet day."""
    pending = cup_pending(season)
    if pending:
        detail = ", ".join(f"{comp} {n}" for comp, n in pending.items())
        args = " ".join(f'"{comp}"' for comp in pending)
        log(f"[matchday] STILL PENDING after the run: {detail}")
        log(f"[matchday]   python -m ingestion.matchday {args}   (VPN OFF, headful)")
    return pending


def _audit_player_coverage(now: dt.datetime, log) -> list[coverage.Gap]:
    """This job owns FBref player ingestion, so it owns the alarm for its gaps
    (ADR 0014). Injectable so unit tests never depend on live DB state."""
    with SessionLocal() as session:
        overdue, _known = coverage.audit(
            session, coverage.PLAYER_FBREF, now=now, log=log
        )
    return overdue


def run_matchday(
    competitions: list[str] | None = None,
    season: str | None = None,
    now: dt.datetime | None = None,
    log=print,
    audit=_audit_player_coverage,
) -> dict:
    """Refresh player data for the planned competitions under the watchdog.

    Each competition runs to completion (or the watchdog's give-up), then — for a
    cup/European competition — its team_match rows are built from the freshly
    cached pages (zero network), and orphaned drivers are swept before the next.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    season = season or season_for(now)

    # Only the default (unnamed) run needs the pending-work probe; an explicit
    # request runs exactly what it names.
    if competitions is None:
        pending_leagues = {
            c
            for c in LEAGUE_PLAYER_COMPETITIONS
            if run_backfill._pending(season, c) > 0
        }
        pending_cups = set(pending_cup_competitions(season, log=log))
    else:
        pending_leagues, pending_cups = set(), set()
    plan = plan_competitions(competitions, pending_leagues, pending_cups)

    if not plan:
        log(f"[matchday] no pending player data for {season}")
        # Audited HERE as well, deliberately. This is the branch that reported
        # "no pending player data" on four consecutive mornings while 6 Premier
        # League and 11 Championship fixtures sat un-ingested: the pending probe
        # only counts FINISHED fixtures, and nothing had marked them finished.
        # An empty plan is a claim that there is no work — the audit is what
        # checks that claim against the data instead of trusting it.
        return {
            "season": season,
            "ran": [],
            "results": {},
            "pending_manual": {},
            "overdue": audit(now, log),
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
        "pending_manual": _log_still_pending(season, log),
        "overdue": audit(now, log),
    }


if __name__ == "__main__":
    report = run_matchday(sys.argv[1:] or None)
    # Non-zero if any competition's watchdog gave up (stall / max restarts), OR
    # if player data is still overdue after the run — including on the
    # "nothing to do" path, where a clean exit is precisely what hid the ADR
    # 0014 failure. "Nothing to do" is only success if the audit agrees.
    sys.exit(
        1
        if any(code != 0 for code in report["results"].values())
        or report.get("overdue")
        else 0
    )
