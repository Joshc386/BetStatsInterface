import { describe, expect, it } from 'vitest'
import { coerceN } from './LastNInput'

describe('coerceN', () => {
  it('blank or non-numeric falls back to 5', () => {
    expect(coerceN('', 100)).toBe(5)
    expect(coerceN('abc', 100)).toBe(5)
    expect(coerceN('  ', 100)).toBe(5)
  })

  it('values below 1 fall back to 5', () => {
    expect(coerceN('0', 100)).toBe(5)
    expect(coerceN('-3', 100)).toBe(5)
  })

  it('valid values pass through', () => {
    expect(coerceN('1', 100)).toBe(1)
    expect(coerceN('20', 100)).toBe(20)
  })

  it('clamps to max', () => {
    expect(coerceN('200', 100)).toBe(100)
    expect(coerceN('40', 30)).toBe(30)
  })
})
