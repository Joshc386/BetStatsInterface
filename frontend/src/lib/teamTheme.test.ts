import { describe, expect, it } from 'vitest'
import {
  AA, CLASH, INK, KITS, PAGE, WHITE, awayTheme, colourDistance, contrast, over,
  teamTheme, themeOf,
} from './teamTheme'

const ids = Object.keys(KITS).map(Number)

// The contract: whatever a club's colours, everything painted with them reads.
function expectReadable(t: ReturnType<typeof teamTheme>) {
  expect(contrast(t.onAccent, t.accent)).toBeGreaterThanOrEqual(AA) // band text
  expect(contrast(t.accentInk, PAGE)).toBeGreaterThanOrEqual(AA) // links, figures
  expect(contrast(t.accentInk, WHITE)).toBeGreaterThanOrEqual(AA) // inside cards
  expect(contrast(t.accentInk, over(t.accent, 0.1, WHITE))).toBeGreaterThanOrEqual(AA) // hit-rate tint
}

describe('team themes', () => {
  it('cover every English league club', () => {
    expect(ids).toHaveLength(101)
  })

  it.each(ids)('club %i reads at WCAG AA everywhere its colours are used', (id) => {
    expectReadable(teamTheme(id))
  })

  it('gives any team without colours the pitch theme index.css paints', () => {
    for (const t of [teamTheme(999_999), teamTheme(null), teamTheme(undefined)]) {
      expect(t.accent).toBe('#2d6a45')
      expect(t.accentInk).toBe('#2d6a45')
      expect(t.onAccent).toBe(WHITE)
    }
  })

  it('lets the trim carry a white-shirted club', () => {
    expect(teamTheme(89).accent).toBe('#132257') // Tottenham: navy, not white
    expect(teamTheme(48).accent).toBe('#1D428A') // Leeds
  })

  it('puts dark text on a light club colour, and reads accents from the trim', () => {
    const watford = teamTheme(92)
    expect(watford.accent).toBe('#FBEE23') // the band stays yellow…
    expect(watford.onAccent).toBe(INK) // …with dark text on it
    expect(watford.accentInk).toBe('#111111') // links go black, not illegible yellow
  })

  it('keeps a club colour untouched when it already reads', () => {
    const chelsea = teamTheme(27)
    expect(chelsea.accent).toBe('#034694')
    expect(chelsea.accentInk).toBe('#034694')
    expect(chelsea.onAccent).toBe(WHITE)
  })

  it('darkens only as far as it must when no kit colour reads (Norwich)', () => {
    const t = themeOf(KITS[64])
    expect(t.accentInk).not.toBe('#00A650')
    expect(contrast(t.accentInk, PAGE)).toBeLessThan(AA + 1) // just over the line, not black
  })
})

describe('change kit on a colour clash', () => {
  it('leaves distinct colours alone', () => {
    expect(awayTheme(teamTheme(5), 27).accent).toBe(teamTheme(27).accent) // Arsenal v Chelsea
  })

  it.each([
    [27, 37, 'Chelsea v Everton'],
    [52, 55, 'Liverpool v Man United'],
    [61, 43, 'Newcastle v Grimsby'],
  ])('switches the away side when %i v %i clash (%s)', (home, away) => {
    const h = teamTheme(home)
    expect(colourDistance(h.accent, teamTheme(away).accent)).toBeLessThan(CLASH)
    expect(colourDistance(h.accent, awayTheme(h, away).accent)).toBeGreaterThanOrEqual(CLASH)
  })

  it('keeps every possible pairing readable and the two sides distinct', () => {
    const clashes: string[] = []
    for (const home of [...ids, null])
      for (const away of [...ids, null]) {
        if (home === away) continue
        const h = teamTheme(home)
        const a = awayTheme(h, away)
        expectReadable(a)
        if (colourDistance(h.accent, a.accent) < CLASH) clashes.push(`${home} v ${away}`)
      }
    expect(clashes).toEqual([])
  })
})
