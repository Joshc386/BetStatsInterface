"""Referee capture from FBref match pages (docs/adr/0018).

A Fixture's Referee is the official the match page names "(Referee)" in its
Officials line — no source gives an id, so identity is the printed name plus
the hand-confirmed REFEREE_ALIASES below. Never merged on a fuzzy match: a
near-duplicate is LISTED for review (`near_duplicates`), never decided.

Two writers, one helper:
- in-season, `record_referee` is called by the league, cup and international
  ingest paths with the page they already hold;
- history, `backfill_referees` reads the cached pages — zero network.

Run:  python -m ingestion.referees          # dry run: report + review list, no writes
      python -m ingestion.referees --apply  # stamp fixtures.referee_id (idempotent)

This module is a LEAF (app.models + stdlib only) so players/cups/internationals
can import it without a cycle; the cache path is imported lazily in the backfill.
"""

from __future__ import annotations

import html as html_lib
import re
import sys
import unicodedata
from collections import defaultdict
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.facts import Fixture
from app.models.reference import Competition, Referee

# Printed spelling -> canonical spelling. Only HAND-CONFIRMED entries: one man
# printed two ways, checked by a human (CONTEXT.md "Referee"). The canonical
# side must itself be a printed spelling, never an invented one.
REFEREE_ALIASES: dict[str, str] = {
    "Sam Allison": "Samuel Allison",  # 5 pages vs 171
    "Steve Martin": "Stephen Martin",  # spelling switched 2023-24; never on the same day in 192 matches
    # Foreign referees seen only in internationals / covered ties: a middle
    # name or accent printed on some pages and not others, no shared match
    # day. Canonical = the most recent printing. Confirmed 2026-10-03.
    "Abdelrahman Al Jassim": "Abdulrahman Ibrahim Al Jassim",
    "Ahmed Al Kaf": "Ahmed Abu Bakar Al Kaf",
    "Ali Sabah Adday Al Qaysi": "Ali Sabah Al Qaysi",
    "Artur Dias": "Artur Soares Dias",
    "Crishantha Dilan Perera": "Dilan Perera",
    "Cumar Cabdiqaadir Cartan": "Cumar Cartan",
    "Donald Robertson": "Don Robertson",
    "Espen Andreas Eskås": "Espen Eskås",
    "Fabricio Duarte": "Fabrício Duarte",
    "Horațiu Fesnic": "Horațiu Feșnic",
    "Jakob A. Sundberg": "Jakob Sundberg",
    "José Sánchez": "Jose Maria Sánchez",
    "Juan Benítez": "Juan Gabriel Benítez",
    "Khamis Al Marri": "Khamis Mohammed Al Marri",
    "Luis Santander": "Luis Enrique Santander",
    "Mahmoud Zakaria El Banna": "Mahmoud El Banna",
    "Marco Ortíz": "Marco Antonio Ortíz",
    "Pierre Atcho": "Pierre Ghislain Atcho",
    "Retselisitsoe Molise": "Retṧelisitsoe Molise",
    "Sadok Selmi": "Sadek Selmi",
    "Vilhjálmur Alvar Þórarinsson": "Vilhjálmur Þórarinsson",
    "Wilton Pereira Sampaio": "Wilton Sampaio",
}

# Same-surname pairs a human has ruled are DIFFERENT people, so the review list
# stops re-asking. Sorted tuples, as near_duplicates emits them.
CONFIRMED_DISTINCT: frozenset[tuple[str, str]] = frozenset({
    ("Ahmad Al Ali", "Ahmed Al Ali"),  # refereed on the same day
    ("Santillan dos Santos", "Sánchez Santos"),  # different names entirely
})

# The Officials line holds each official as `<span …>Name (Role)</span>`; the
# Referee is the one whose role is exactly "Referee" (not AR1/AR2/4th/VAR).
_REFEREE_SPAN = re.compile(r"<span[^>]*>([^<]*?)\s*\(Referee\)\s*</span>")

# The Officials line sits in the scorebox, well inside the first ~100 KB of a
# page; reading the whole page made a full-cache scan time out (handoff evidence).
_HEAD_BYTES = 140_000


def parse_referee(match_html: str) -> str | None:
    """Return the Referee's name from a match page, or None if it names none."""
    m = _REFEREE_SPAN.search(match_html)
    if not m:
        return None
    name = " ".join(html_lib.unescape(m.group(1)).replace("\xa0", " ").split())
    return name or None


def canonical_referee(name: str) -> str:
    """Fold a hand-confirmed second spelling onto its canonical name."""
    return REFEREE_ALIASES.get(name, name)


def _fold(token: str) -> str:
    """Lowercase, accent-free, letters only — for grouping, never for identity."""
    decomposed = unicodedata.normalize("NFKD", token)
    return "".join(c for c in decomposed if c.isalpha()).lower()


def _compatible(a: str, b: str) -> bool:
    """Could these two printed names be one man? Only ever a reason to ASK.

    Yes when one name's words all appear in the other (a dropped middle name,
    or an accent-only difference once folded), or when the first names agree
    in shape: an initial ("S."), a prefix ("Sam"/"Samuel"), or a shared stem
    of 3+ letters ("Steve"/"Stephen").
    """
    ta = [_fold(t) for t in a.split()]
    tb = [_fold(t) for t in b.split()]
    if set(ta) <= set(tb) or set(tb) <= set(ta):
        return True
    fa, fb = ta[0], tb[0]
    if not fa or not fb or fa[0] != fb[0]:
        return False
    stem = 0
    for x, y in zip(fa, fb):
        if x != y:
            break
        stem += 1
    return min(len(fa), len(fb)) == 1 or stem == min(len(fa), len(fb)) or stem >= 3


def near_duplicates(names: list[str]) -> list[tuple[str, str]]:
    """Pairs of distinct printed names that might be one man, for a human to rule.

    Same surname (accent-folded) AND compatible first names — CONTEXT.md's
    definition. A shared surname alone is not enough: Elliot and James Bell are
    two men, and listing every Hernandez would bury the pairs that matter.
    Listed, never merged. Sorted pairs, sorted list, so runs diff cleanly.
    """
    by_surname: dict[str, set[str]] = defaultdict(set)
    for name in names:
        parts = name.split()
        if parts:
            by_surname[_fold(parts[-1])].add(name)
    return sorted(
        pair
        for group in by_surname.values()
        for pair in combinations(sorted(group), 2)
        if _compatible(*pair)
    )


def _referee_id(session: Session, name: str) -> int:
    """Get-or-create a referee by canonical name; return its id."""
    session.execute(
        insert(Referee).values(name=name).on_conflict_do_nothing(
            constraint="uq_referees_name"
        )
    )
    return session.scalar(select(Referee.id).where(Referee.name == name))


def record_referee(session: Session, fixture: Fixture, match_html: str) -> str | None:
    """Stamp `fixture.referee_id` from its match page; return the name stamped.

    Never raises on a page that names no referee — that leaves the column as it
    was (an honest NULL), and must not cost the match its player rows. Runs in
    the caller's transaction, so it commits or rolls back with them.
    """
    name = parse_referee(match_html)
    if name is None:
        return None
    name = canonical_referee(name)
    fixture.referee_id = _referee_id(session, name)
    return name


def _read_head(path) -> str:
    with open(path, "rb") as fh:
        return fh.read(_HEAD_BYTES).decode("utf-8", errors="ignore")


def backfill_referees(apply: bool = False, log=print) -> dict:
    """Stamp every finished, FBref-linked Fixture's referee from the cache.

    Zero network: a page absent from the cache is counted and reported, never
    fetched. Prints the near-duplicate review list with each name's evidence
    (matches, date range, scopes) — overlapping dates prove two people.
    Idempotent: a re-run re-stamps the same ids, so a new alias takes effect
    by re-running.
    """
    from ingestion.players import _FBREF_CACHE  # lazy: keeps this module a leaf

    with SessionLocal() as session:
        rows = session.execute(
            select(Fixture.id, Fixture.fbref_match_id, Fixture.date, Competition.type)
            .join(Competition, Competition.id == Fixture.competition_id)
            .where(Fixture.status == "finished", Fixture.fbref_match_id.is_not(None))
            .order_by(Fixture.date)
        ).all()

        named: dict[int, str] = {}
        printed: dict[str, int] = defaultdict(int)
        missing_cache = no_referee = 0
        evidence: dict[str, dict] = {}
        for fixture_id, game_id, date, ctype in rows:
            path = _FBREF_CACHE / f"match_{game_id}.html"
            if not path.exists():
                missing_cache += 1
                continue
            raw = parse_referee(_read_head(path))
            if raw is None:
                no_referee += 1
                continue
            printed[raw] += 1
            name = canonical_referee(raw)
            named[fixture_id] = name
            ev = evidence.setdefault(
                raw, {"n": 0, "first": date, "last": date, "scopes": set(), "dates": set()}
            )
            ev["n"] += 1
            ev["last"] = date
            ev["scopes"].add(ctype)
            ev["dates"].add(date.date())

        log(f"finished FBref-linked fixtures: {len(rows)}")
        log(f"  referee named:      {len(named)}")
        log(f"  page names no one:  {no_referee}")
        log(f"  page not in cache:  {missing_cache}")
        log(f"  distinct printed names: {len(printed)} -> "
            f"{len(set(named.values()))} referees after aliases")

        review = [
            p for p in near_duplicates(list(printed))
            if p not in CONFIRMED_DISTINCT
            and canonical_referee(p[0]) != canonical_referee(p[1])
        ]
        log(f"\nnear-duplicate review list ({len(review)} pair(s); "
            "add to REFEREE_ALIASES or CONFIRMED_DISTINCT, then re-run):")
        for a, b in review:
            shared = len(evidence[a]["dates"] & evidence[b]["dates"])
            for name in (a, b):
                ev = evidence[name]
                log(f"  {name:<28} {ev['n']:>4} matches  "
                    f"{ev['first']:%Y-%m-%d} .. {ev['last']:%Y-%m-%d}  "
                    f"{', '.join(sorted(ev['scopes']))}")
            verdict = (f"DIFFERENT PEOPLE: both refereed on {shared} same day(s)"
                       if shared else "no shared match day")
            log(f"    -> {verdict}\n")

        if apply:
            ids = {name: _referee_id(session, name) for name in set(named.values())}
            for fixture_id, name in named.items():
                session.get(Fixture, fixture_id).referee_id = ids[name]
            session.commit()
            log(f"applied: {len(named)} fixtures stamped, {len(ids)} referees")
        else:
            log("dry run: nothing written (re-run with --apply)")

        return {
            "fixtures": len(rows),
            "named": len(named),
            "no_referee": no_referee,
            "missing_cache": missing_cache,
            "referees": len(set(named.values())),
            "review": review,
        }


if __name__ == "__main__":
    # Foreign referees' names (Romanian, Turkish, ...) are outside cp1252; the
    # Windows console would crash mid-report on the first one.
    sys.stdout.reconfigure(encoding="utf-8")
    backfill_referees(apply="--apply" in sys.argv)
