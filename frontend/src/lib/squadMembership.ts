// Squad-panel ordering and self-labelling (docs/adr/0013).
//
// Extracted from SquadForm.tsx purely so it can be tested: the repo runs vitest
// with no DOM environment, so logic has to live outside the component to be
// reachable. Nothing here imports React.

export type Membership = 'squad' | 'recent'

export interface Sortable {
  /** The member's headline figure — hit-rate % when thresholded, else the
   * per-app average. null when his window is empty and the panel shows "—". */
  sortKey: number | null
  /** His most-recent appearance for this club (ISO), or null when we hold none. */
  last_seen: string | null
}

/** Most-recent appearance first; a member we have never seen sorts last.
 * ISO-8601 strings compare correctly lexically, so no Date parsing. */
function compareLastSeen(a: Sortable, b: Sortable): number {
  if (a.last_seen === b.last_seen) return 0
  if (a.last_seen === null) return 1
  if (b.last_seen === null) return -1
  return a.last_seen < b.last_seen ? 1 : -1
}

/** Panel order: biggest figure first, every member without a figure below every
 * member with one, ties broken by last_seen.
 *
 * The no-figure group is ADR 0013's addition — a Squad member we hold no
 * appearances for. It is deliberately not folded into the numeric comparison
 * with a sentinel: the previous `b.sortKey - a.sortKey` used -Infinity, and two
 * such members yielded `-Infinity - -Infinity` = NaN, which is an undefined
 * comparator result. Before 0013 that group was always empty, so it never bit.
 */
export function compareByFigure(a: Sortable, b: Sortable): number {
  if (a.sortKey === null || b.sortKey === null) {
    if (a.sortKey !== null) return -1
    if (b.sortKey !== null) return 1
    return compareLastSeen(a, b)
  }
  if (a.sortKey !== b.sortKey) return b.sortKey - a.sortKey
  return compareLastSeen(a, b)
}

/** How the panel describes itself. `recent` must never read as a registered
 * squad — it is the appearance-derived fallback for a club with no roster, and
 * carries ADR 0006's staleness ("ghosts") that 0013 exists to remove. */
export function membershipLabel(
  membership: Membership,
  count: number,
): { count: string; caption: string } {
  if (membership === 'squad') {
    return {
      count: `${count} in squad`,
      caption:
        'Registered squad, plus anyone who has played in the last 30 days. ' +
        '“Last seen” is their most recent appearance for this club — “—” means we hold none.',
    }
  }
  return {
    count: `${count} in recent squad`,
    caption:
      'No registered squad for this club — showing players whose most recent ' +
      'appearance was for them, so departures linger. “Last seen” flags them.',
  }
}
