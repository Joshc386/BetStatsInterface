# Schedule and roster sourcing for fixtures and Championship player data

**Status:** accepted

Two needs surfaced while planning the Championship player backfill and the fixture
view. (1) Player ingestion needs FBref `game_id`s to fetch a match's player page;
the existing pipeline gets them from `soccerdata`'s `FBref.read_schedule()`, which
returns nothing for the Championship — it enumerates seasons via
`read_seasons(split_up_big5=True)`, empty for non-Big-5 leagues, so no page is even
fetched. (2) The fixture view needs *upcoming* (`status='scheduled'`) fixtures, which
no current ingestion produces — `team_match.py` only ever writes `status='finished'`
rows from football-data.co.uk results CSVs. Separately, the fixture view's squad-form
panel needs to know *who is at a club now* (a new signing who has not played; not a
player loaned out) — a roster fact that appearance history cannot express.

## Decision

**Split the schedule sources by what each is for:**

- **Finished-match `game_id`s → custom FBref Scores & Fixtures reader.** A new
  `ingestion/fbref_schedule.py` replicates `read_schedule`'s fetch+parse but fixes the
  broken season enumeration, calling `soccerdata`'s own `fb.get(url, filepath)` so the
  rate limiter and on-disk cache are **not** bypassed (non-negotiable #3). It runs
  inside the existing persistent, Cloudflare-solved `fb` session used by the player
  backfill, sharing one solve. Championship fixtures already exist (from
  football-data.co.uk) with `fbref_match_id = NULL`; the reader's output feeds the
  unchanged `link_fixtures()` to stamp the ids, then the unchanged per-match pipeline.
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

- Ingestion now has a custom FBref reader alongside `soccerdata`'s built-ins; it must
  track FBref's Scores & Fixtures markup and is covered by a parse unit test against a
  saved page (same discipline as `parse_player_ids`).
- Each new Championship season still needs its FBref team aliases pre-derived in
  `FBREF_TEAM_ALIASES` before backfill (`link_fixtures` is fail-loud) — extends ADR
  0001's three-way reconciliation to the 24 Championship clubs.
- A nightly job (Job B) and a weekly roster job (Job C) are now load-bearing for the
  fixture view's "upcoming" and "current squad" accuracy; both are incremental and
  cache/rate-limit-respecting per the standing non-negotiables.
- The `squads` table (already in the schema, unused until now) becomes populated; the
  fixture view reads it for membership and never auto-derives a starting XI.
