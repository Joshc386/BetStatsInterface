# Fixture comparison via a raw-rows endpoint with client-side aggregation

**Status:** accepted

The Fixture view (Phase G) compares two teams across the full team-metric set
(~13 metrics) in two modes — **Team form** (each team's last-N games vs all
opponents, side by side) and **Head-to-Head** (their past meetings) — with a
selectable window N, a per-team Home/Away/Recent venue toggle, and adjustable
hit-rate thresholds. Naively that is ~13 metrics × 3 perspectives (A, B, H2H) =
~39 figures, each recomputed whenever the user changes N, venue, or a threshold.

The existing `entity_summary` computes one metric's Summary Metric server-side
for one entity. Driving the Fixture view through it means either ~39 calls per
load, or a composite endpoint that loops `entity_summary` per metric — and
either way every venue/threshold tweak is a server param, forcing a refetch.

## Decision

**One composite endpoint returns raw rows; the client computes everything.**

`GET /fixtures/compare?home={A}&away={B}&n={N}` runs **three filtered
`team_match` queries** — A's last-N, B's last-N, and the A-vs-B meetings — and
returns the raw rows (every Metric column plus the GENERATED booleans `btts`,
`clean_sheet`, `total_goals`, `result`, plus each row's date / competition /
venue / score). The frontend renders both modes, all ~13 metrics' A/B/H2H
figures, the per-meeting breakdown, and recomputes hit-rates, averages, venue
filters, and threshold changes **client-side, with no further server calls**.
Only changing N or the team selection refetches.

A small, unit-tested TypeScript helper mirrors `entity_summary`'s hit-rate /
average math; the Python `entity_summary` remains the oracle and is unchanged
(it still powers the single-metric Entity view / team hub). The drill-down
(`GET /fixtures/{id}`) returns both teams' full `team_match` rows for one match.
This **retires `GET /teams/{id}/h2h/{opp}`** (the composite subsumes it).

## Considered options

- **~39 small `/teams/{id}/summary` calls** — rejected. Excessive round-trips
  per load and a refetch storm on every window/venue/threshold change.
- **Composite endpoint returning server-computed per-metric summaries** —
  rejected. Keeps one stats implementation (Python), but needs a per-metric
  aggregation loop server-side AND a refetch on every threshold/venue tweak,
  losing the instant interactivity that makes the table worth having.
- **Materialise rolling aggregates** — rejected, consistent with the standing
  convention to compute windows at query time and not pre-store rolling
  features; there is no perf problem to justify it.

## Consequences

- **Leanest backend**: three `SELECT … WHERE … ORDER BY date DESC LIMIT N`
  queries, no per-metric loop. Payloads are tiny (three sets of ~10–20
  small-int rows), which is exactly why client-side aggregation is acceptable —
  and this is a single-user, local tool, so the trade-off is clearly worth it.
- **Snappy UI**: venue toggles and threshold drags recompute locally; only N /
  team changes hit the server.
- **Cost**: the hit-rate / average math is duplicated in TypeScript. Mitigated
  by keeping it a small pure function with its own unit tests, cross-checked
  against the Python `entity_summary` (the regression oracle). Note: this is a
  flat aggregation over an already-selected row set — NOT the leakage-aware
  rolling-window SQL, which stays server-side via the `LIMIT N` row selection.
- `entity_summary` stays the single server-side stats path for the team hub;
  the Fixture view is its own raw-rows path. Two readers, one fact source.
- Venue and threshold are never server params; the API surface stays minimal
  (window N + the two team ids).
