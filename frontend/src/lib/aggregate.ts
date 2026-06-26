// Client-side Summary Metric math for the Fixture view (docs/adr/0005).
// Mirrors the Python entity_summary (backend/app/stats.py), which is the
// regression oracle — keep the two in step.

export type MetricKind = 'count' | 'bool'
export type Direction = 'over' | 'under'

export interface Agg {
  games: number // rows considered (nulls included)
  total: number // counts: sum; bools: count of true
  average: number | null // counts: mean; bools: fraction true (0..1)
  hits: number | null // vs threshold, else null
  pct: number | null // 100 * hits / non-null, rounded 1dp, else null
}

export interface SummariseOpts {
  kind: MetricKind
  threshold?: number
  direction?: Direction
}

/** Aggregate one metric's per-game values over a set of rows. */
export function summarise(
  values: Array<number | boolean | null>,
  opts: SummariseOpts,
): Agg {
  const present = values.filter((v): v is number | boolean => v !== null && v !== undefined)
  const games = values.length
  const n = present.length

  let total: number
  if (opts.kind === 'bool') {
    total = present.filter(Boolean).length
  } else {
    total = present.reduce((sum: number, v) => sum + Number(v), 0)
  }
  const average = n ? total / n : null

  let hits: number | null = null
  let pct: number | null = null
  if (opts.threshold !== undefined && n) {
    const direction = opts.direction ?? 'over'
    if (opts.kind === 'bool') {
      const want = direction !== 'under'
      hits = present.filter((v) => Boolean(v) === want).length
    } else if (direction === 'under') {
      hits = present.filter((v) => Number(v) <= opts.threshold!).length
    } else {
      hits = present.filter((v) => Number(v) >= opts.threshold!).length
    }
    pct = Math.round((1000 * hits) / n) / 10
  }

  return { games, total, average, hits, pct }
}
