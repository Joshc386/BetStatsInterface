# Schedule and roster sourcing for fixtures and Championship player data

**Status:** accepted

Two needs surfaced while planning the Championship player backfill and the fixture
view. (1) Player ingestion needs FBref `game_id`s to fetch a match's player page;
the existing pipeline gets them from `soccerdata`'s `FBref.read_schedule()`, which
returned nothing for the Championship. The original hypothesis (a `split_up_big5`
season-enumeration quirk) was **wrong**: the real cause is a league-name mismatch in
`soccerdata`'s `league_dict` — it maps `ENG-Championship` to the FBref source name
`"Championship"`, but FBref's competition index lists the English second tier as
`"EFL Championship"` (comp id 10; there are also Scottish/USL/CONCACAF "Championship"
leagues). `_translate_league` therefore matched no row, `read_leagues` returned empty,
and the empty propagated to the `pd.concat` in `read_seasons` ("No objects to
concatenate"). (2) The fixture view needs *upcoming* (`status='scheduled'`) fixtures, which
no current ingestion produces — `team_match.py` only ever writes `status='finished'`
rows from football-data.co.uk results CSVs. Separately, the fixture view's squad-form
panel needs to know *who is at a club now* (a new signing who has not played; not a
player loaned out) — a roster fact that appearance history cannot express.

## Decision

**Split the schedule sources by what each is for:**

- **Finished-match `game_id`s → corrected `league_dict` mapping; no custom reader.**
  The fix is one line: map `ENG-Championship` to FBref source name `"EFL Championship"`
  in the repo's `soccerdata_config/league_dict.json` (synced by `config_sync`). With
  that, `soccerdata`'s own `read_schedule()` works unchanged — verified live: it returns
  all 557 Championship 2025-26 games with a populated `game_id` for every row. The
  entire existing player pipeline (`read_schedule` → `link_fixtures` → `_pending_fixtures`
  → `parse_player_ids` → `ingest_match`) then runs verbatim; Championship fixtures
  already exist (from football-data.co.uk) with `fbref_match_id = NULL` and just get
  stamped. The earlier plan to hand-roll `ingestion/fbref_schedule.py` was dropped once
  the root cause turned out to be config, not soccerdata's fetch path — the leanest fix
  reuses the rate-limited, cached, Cloudflare-aware reader we already have.
- **Upcoming scheduled fixtures → football-data.co.uk `fixtures.csv`.** Free, no rate
  limit, uses the team names we already reconcile, refreshable nightly in Job B. FBref
  is not used for the upcoming feed.

**Squad membership → roster job (Job C) from FBref squad pages**, into the existing
`squads` table. Membership = currently-registered squad (new signings in, loaned-out
out, per FBref's squad page). The squad decides *who* the squad-form panel shows;
Appearances supply *the numbers*. The fixture view is routed by team-vs-team
(`/fixture/:homeId/vs/:awayId`), so it needs neither a real scheduled `fixtures` row
nor the upcoming feed to be built and verified — both panels and H2H work from two
team ids + scope.

## Considered options

- **Custom `ingestion/fbref_schedule.py` reader** (replicate `read_schedule`'s
  fetch+parse, fixing enumeration ourselves) — planned, then rejected once the root
  cause was found to be a one-line config mismatch. A hand-rolled reader would have been
  new code to maintain against FBref markup for no benefit over the corrected mapping.
- **FBref as the single schedule source** (parse both finished and upcoming rows from
  the one page, upsert scheduled fixtures too) — rejected. It drags Cloudflare and the
  FBref rate limit into the nightly upcoming-fixtures refresh for no gain, when
  football-data.co.uk gives the same upcoming feed for free.
- **Appearance-derived squad** (members = players with a recent `player_match` for the
  team) — rejected as the membership rule. It cannot include an unplayed new signing,
  cannot exclude a player loaned out at a season boundary, and collapses to empty in
  the off-season. Kept only for the *form numbers*, not membership.
- **Transfermarkt for precise loan/contract status** — deferred, not adopted. It is a
  fourth source needing its own id reconciliation; FBref's squad-page view of loans is
  good enough for v1.

## Consequences

- The Championship now rides the **unchanged** player pipeline; the only delta is the
  corrected `league_dict` entry (version-controlled in the repo, synced by
  `config_sync`). No new fetch/parse code to maintain. The standing risk is that FBref
  could relabel a competition again — a config edit, not a code change.
- Each new Championship season still needs its FBref team aliases pre-derived in
  `FBREF_TEAM_ALIASES` before backfill (`link_fixtures` is fail-loud) — extends ADR
  0001's three-way reconciliation to the Championship clubs (9 added for 2025-26;
  promotion/relegation means earlier seasons add their own before backfill).
- A nightly job (Job B) and a weekly roster job (Job C) are now load-bearing for the
  fixture view's "upcoming" and "current squad" accuracy; both are incremental and
  cache/rate-limit-respecting per the standing non-negotiables.
- The `squads` table (already in the schema, unused until now) becomes populated; the
  fixture view reads it for membership and never auto-derives a starting XI.
