import { describe, it, expect } from 'vitest'
import { compareByFigure, membershipLabel, type Sortable } from './squadMembership'

// A member is `{ figure, last_seen }`: `figure` is the sort value derived from
// his window (hit-rate % or per-app average), null when the window is empty.
const m = (figure: number | null, last_seen: string | null = null): Sortable => ({
  sortKey: figure,
  last_seen,
})

const order = (rows: Sortable[]) => [...rows].sort(compareByFigure)

describe('compareByFigure', () => {
  it('ranks a higher figure first', () => {
    expect(order([m(1.2), m(3.4), m(2.0)]).map((r) => r.sortKey)).toEqual([3.4, 2.0, 1.2])
  })

  it('ranks any figure above no figure, including zero and negatives', () => {
    expect(order([m(null), m(0)]).map((r) => r.sortKey)).toEqual([0, null])
    expect(order([m(null), m(-1)]).map((r) => r.sortKey)).toEqual([-1, null])
  })

  // The ADR 0013 case: a Squad member we hold no appearances for sorts last,
  // never above someone with a real figure.
  it('sinks every no-figure member below every figured one', () => {
    const rows = [m(null, null), m(0.5, '2026-08-01'), m(null, '2024-01-01'), m(0, '2026-07-01')]
    expect(order(rows).map((r) => r.sortKey)).toEqual([0.5, 0, null, null])
  })

  // Regression: the old comparator was `b.sortKey - a.sortKey` with -Infinity as
  // the no-figure sentinel, so two no-figure members produced NaN — an undefined
  // comparator result. ADR 0013 made that group reachable for the first time.
  it('never returns NaN when both members lack a figure', () => {
    expect(compareByFigure(m(null), m(null))).not.toBeNaN()
    expect(Number.isFinite(compareByFigure(m(null), m(null)))).toBe(true)
  })

  it('breaks a figure tie by last_seen, most recent first', () => {
    const rows = [m(2, '2026-01-01'), m(2, '2026-08-01'), m(2, '2025-05-05')]
    expect(order(rows).map((r) => r.last_seen)).toEqual([
      '2026-08-01',
      '2026-01-01',
      '2025-05-05',
    ])
  })

  // "In the squad, nothing known" is the least informative row on the panel, so
  // it sits below a member who at least played for the club at some point.
  it('puts a null last_seen below a known one within the no-figure group', () => {
    const rows = [m(null, null), m(null, '2023-04-01'), m(null, null), m(null, '2026-08-01')]
    expect(order(rows).map((r) => r.last_seen)).toEqual([
      '2026-08-01',
      '2023-04-01',
      null,
      null,
    ])
  })

  it('is symmetric', () => {
    const pairs: Array<[Sortable, Sortable]> = [
      [m(1), m(2)],
      [m(null), m(2)],
      [m(null, '2026-01-01'), m(null, null)],
      [m(2, '2026-01-01'), m(2, '2026-01-01')],
    ]
    for (const [a, b] of pairs) {
      // summed rather than negated: -Math.sign(0) is -0, which toBe rejects
      // against +0 under Object.is.
      expect(Math.sign(compareByFigure(a, b)) + Math.sign(compareByFigure(b, a))).toBe(0)
    }
  })
})

describe('membershipLabel', () => {
  it('calls a roster-backed panel a squad', () => {
    const l = membershipLabel('squad', 32)
    expect(l.count).toBe('32 in squad')
    expect(l.caption).toMatch(/registered squad/i)
  })

  it('says plainly when it is the appearance-derived fallback', () => {
    const l = membershipLabel('recent', 71)
    expect(l.count).toBe('71 in recent squad')
    // must not imply a roster it does not have
    expect(l.caption).toMatch(/no registered squad/i)
  })

  it('never claims a registered squad on the fallback', () => {
    expect(membershipLabel('recent', 5).count).not.toBe('5 in squad')
  })
})
