/** The W/D/L badge. One definition so a match reads the same everywhere —
 * the fixture form lists and the team/player breakdown tables. */
export const resultClass = (r: string | null | undefined) =>
  r === 'W' ? 'bg-emerald-100 text-emerald-800'
    : r === 'L' ? 'bg-rose-100 text-rose-800'
      : 'bg-stone-200 text-stone-700'

export function ResultChip({ result }: { result: string | null | undefined }) {
  // No team row for this appearance -> hold the column's width, print nothing.
  if (!result) return <span className="inline-block h-5 w-5" />
  return (
    <span
      className={`grid h-5 w-5 shrink-0 place-items-center rounded text-xs font-bold ${resultClass(result)}`}
    >
      {result}
    </span>
  )
}

/** Proportion of the window's largest value, as a 0-1 fraction. Guards the
 * all-zero window (and a bool metric's 0/1, where it reads as on/off). */
export function barFraction(value: number | null | undefined, max: number) {
  if (value === null || value === undefined || max <= 0) return 0
  return Math.max(0, Math.min(1, value / max))
}

/** A quiet bar behind the number, so a run of games shows its shape at a
 * glance instead of making you read every cell. */
export function ValueBar({ fraction }: { fraction: number }) {
  return (
    <span className="block h-1 w-full overflow-hidden rounded-full bg-line-soft">
      <span
        className="block h-full rounded-full bg-accent-ink"
        style={{ width: `${fraction * 100}%` }}
      />
    </span>
  )
}
