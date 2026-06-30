# Team identity via `fbref_id`; deterministic cross-source seam reconciliation

**Status:** accepted

Teams have always been reconciled across sources by **name** — `resolve_fbref_team`
maps an FBref display name through `FBREF_TEAM_ALIASES` to a `canonical_name`, and
`clean_name` only collapses whitespace. The `teams.fbref_id` column exists but is
**unused (0 of 100 rows populated)**. This was tolerable while every team originated
from football-data.co.uk (league-only, a small stable set) and FBref names were
hand-aliased before each backfill. Adding domestic-cup data (ADR 0008) breaks that
tolerance: cup draws are random, opponents are discovered at fetch time, and some are
non-league clubs absent from `teams` that must be **auto-created**. Name-only identity
then risks silent duplicates — an auto-created "Salford City" colliding with a later
football-data.co.uk "Salford", or "Arsenal" vs "Arsenal FC" splitting one club in two.
Players already avoid this: `_upsert_player` keys on a stable `fbref_id`, so a player
is one row regardless of accent/spelling variation. Teams should match that robustness.

## Decision

**Make `fbref_id` the identity spine for every FBref-sourced team; keep one canonical
row per club carrying all source handles; link across the source seam deterministically.**

- The canonical `teams` row holds `fbref_id` (the FBref spine), `fdcouk_name` (the
  football-data.co.uk handle), and `canonical_name` (display). One row per real club.
- **Resolve by the source's own key first:** FBref data resolves by `fbref_id`;
  football-data.co.uk data resolves by `fdcouk_name`. Both are robust once populated.
- **Seam linking (on a key miss):** attempt to link to an existing row by
  **normalised name** (accent-fold, strip `FC`/`AFC`/club-suffixes, lowercase) plus the
  **explicit alias map** for cases normalisation cannot reach (`"Man Utd"` →
  `"Manchester United"`). On a match, **attach the missing handle to that row** — never
  create a second. Only create a new row if there is no match (fail-loud for a covered
  FBref club; auto-create + log for a cup opponent, per ADR 0008).
- **Backfill the existing 100 teams' `fbref_id`** from the *already-cached* FBref match
  pages — the squad tables are `stats_<fbref_id>_summary`, so the id is extractable with
  no new fetch — resolving each FBref name to its canonical row via the alias map that
  already works for PL/Championship.
- A **duplicate-detection guard/report** surfaces anything the matcher misses (two rows
  sharing a normalised name; an `fbref_id`-bearing row whose name matches an incoming
  football-data.co.uk name) loudly, rather than letting it split silently.

The team backfill makes `fbref_id` the FBref-side key; football-data.co.uk stays
`fdcouk_name`-keyed (it publishes no ids) but is linked onto the same fbref-spined row.

## Considered options

- **Keep name-only reconciliation** — rejected. It is the source of the duplicate risk
  that cup auto-creation makes acute; players already proved id-keying is the fix.
- **Fuzzy (edit-distance) seam linking** — rejected. Auto-merging near-matches risks
  fusing genuinely different clubs (reserve/U21 sides, same-town clubs) — the precise
  data-integrity failure this ADR exists to prevent. Linking is **deterministic only**
  (normalised-exact or explicit alias); everything ambiguous goes to the review report.
- **Backfill `fbref_id` via fresh FBref fetches** — rejected as unnecessary; the ids are
  already in the cached match HTML, so the backfill is zero-network.

## Consequences

- Auto-created cup opponents carry a stable `fbref_id`; if one later enters the league
  via football-data.co.uk, the seam matcher attaches `fdcouk_name` to the existing row
  instead of duplicating. Symmetric for a promoted league club later seen on FBref.
- `FBREF_TEAM_ALIASES` shrinks from "load-bearing for every team" to a fallback for the
  abbreviation cases normalisation cannot resolve.
- Extends ADR 0001's three-way reconciliation: the FBref↔canonical leg moves from
  name-based to id-based; the football-data.co.uk↔canonical leg stays name-based by
  necessity (no ids) but is anchored to the same canonical row.
- A standing dup-guard test joins the report logic so future seam misses fail CI rather
  than splitting a club silently — mirroring the regression-guard pattern from ADR 0004.
