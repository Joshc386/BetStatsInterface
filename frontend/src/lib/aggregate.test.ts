import { describe, expect, it } from 'vitest'
import { summarise } from './aggregate'

// Known input -> known output. These mirror the Python entity_summary math
// (backend/app/stats.py), which is the regression oracle (see docs/adr/0005).
describe('summarise', () => {
  it('counts: total / average ignore nulls; no threshold => no hit-rate', () => {
    const a = summarise([1, 2, 3, null, 5], { kind: 'count' })
    expect(a.games).toBe(5) // rows considered, nulls included
    expect(a.total).toBe(11)
    expect(a.average).toBeCloseTo(11 / 4) // averaged over 4 non-null
    expect(a.hits).toBeNull()
    expect(a.pct).toBeNull()
  })

  it('counts: over-threshold hit-rate counts values >= threshold', () => {
    const a = summarise([1, 2, 3, 5], { kind: 'count', threshold: 2, direction: 'over' })
    expect(a.hits).toBe(3) // 2, 3, 5
    expect(a.pct).toBe(75)
  })

  it('counts: under-threshold hit-rate counts values <= threshold', () => {
    const a = summarise([1, 2, 3, 5], { kind: 'count', threshold: 2, direction: 'under' })
    expect(a.hits).toBe(2) // 1, 2
    expect(a.pct).toBe(50)
  })

  it('bools: total is count of true; average is the fraction', () => {
    const a = summarise([true, false, true, null], { kind: 'bool' })
    expect(a.games).toBe(4)
    expect(a.total).toBe(2)
    expect(a.average).toBeCloseTo(2 / 3) // over 3 non-null
  })

  it('bools: a threshold (over) means "how often true"', () => {
    const a = summarise([true, false, true], { kind: 'bool', threshold: 1, direction: 'over' })
    expect(a.hits).toBe(2)
    expect(a.pct).toBeCloseTo(66.7)
  })

  it('empty set: average null, no hit-rate, no divide-by-zero', () => {
    const a = summarise([], { kind: 'count', threshold: 1, direction: 'over' })
    expect(a.games).toBe(0)
    expect(a.total).toBe(0)
    expect(a.average).toBeNull()
    expect(a.pct).toBeNull()
  })
})
