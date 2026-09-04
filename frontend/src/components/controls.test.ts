/** The sample note on a rate tile (docs/adr/0016).
 *
 * A rate whose denominator is invisible cannot be checked, and after ADR 0016 a
 * player's per-90 no longer divides by the minutes shown next to it whenever the
 * source failed to publish the metric for some game in the window. The note is
 * what closes that gap — but only when there IS a gap, or it becomes furniture
 * nobody reads.
 */

import { describe, expect, it } from 'vitest'

import { sampleNote } from './controls'

describe('sampleNote', () => {
  it('states the sample when it is smaller than the window', () => {
    expect(sampleNote(7, 10, 'games')).toBe('over 7 of 10 games')
    expect(sampleNote(523, 793, 'mins')).toBe('over 523 of 793 mins')
  })

  it('stays silent when every game was recorded', () => {
    // The common case. A note on every tile is noise, and noise stops being read
    // exactly when it finally means something.
    expect(sampleNote(10, 10, 'games')).toBeUndefined()
  })

  it('stays silent for a team payload, which has no minutes', () => {
    expect(sampleNote(null, 793, 'mins')).toBeUndefined()
    expect(sampleNote(7, null, 'games')).toBeUndefined()
  })

  it('says so when nothing in the window was recorded', () => {
    // The degenerate case: the figure itself renders as "—", and this explains
    // why rather than leaving it looking like a missing player.
    expect(sampleNote(0, 5, 'games')).toBe('over 0 of 5 games')
  })

  it('groups thousands, since a career window runs to five figures of minutes', () => {
    expect(sampleNote(4391, 7189, 'mins')).toBe('over 4,391 of 7,189 mins')
  })
})
