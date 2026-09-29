import type { ReactNode } from 'react'

/** Shared control + readout primitives.
 *
 * These were byte-identical copies in EntityView, PlayerView and SquadForm,
 * which is why the pages drifted apart visually and why a label bug once had to
 * be fixed twice (commit fed030d). One definition each, so weight changes land
 * everywhere at once.
 *
 * The hierarchy they encode: controls are chrome and recede; the readouts are
 * the answer and come forward.
 */

/** One input/select. */
export const ctrl =
  'rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink outline-none focus:border-accent-ink'

/** The filter toolbar. Deliberately has no card fill — it used to share the
 * exact surface of the stat tiles, so eight filters carried the same visual
 * weight as the number you came to read. A single hairline rule is enough to
 * bound it. */
export function ControlBar({ children }: { children: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end gap-x-5 gap-y-3 border-b border-line pb-4">
      {children}
    </div>
  )
}

/** A cluster of related fields inside a ControlBar (what → window → filters →
 * hit-rate). Separators come from the group, so adding one cannot get the
 * dividers wrong. */
export function ControlGroup({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end gap-3 border-line pl-5 first:border-0 first:pl-0 sm:border-l">
      {children}
    </div>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-muted">{label}</span>
      {children}
    </label>
  )
}

export function Toggle({
  value, onChange, options,
}: {
  value: string
  onChange: (v: string) => void
  options: Array<[string, string]>
}) {
  return (
    <div className="inline-flex overflow-hidden rounded-md border border-line">
      {options.map(([v, text]) => (
        <button
          key={v}
          onClick={() => onChange(v)}
          className={`px-3 py-1.5 text-sm ${
            value === v ? 'bg-accent text-on-accent' : 'bg-card text-ink-2 hover:bg-sunken'
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  )
}

/** The `note` for a rate tile: what the rate was actually divided by, stated
 * only when that is smaller than the window.
 *
 * Silent in the common case — most windows have every game recorded, and a note
 * on every tile would be noise that stops being read. It appears exactly when
 * the denominator is not the one the window implies, which is the case a reader
 * would otherwise have no way to spot (docs/adr/0016). */
export function sampleNote(
  recorded: number | null,
  whole: number | null,
  unit: string,
): string | undefined {
  if (recorded == null || whole == null || recorded >= whole) return undefined
  return `over ${recorded.toLocaleString()} of ${whole.toLocaleString()} ${unit}`
}

/** A headline figure. Solid surface + larger numeral so it outranks the
 * toolbar above it.
 *
 * `note` states the sample a rate was computed over when that is smaller than
 * the window — the source did not publish the metric for every game, so the
 * denominator is not the one the window implies (docs/adr/0016). Shown for the
 * same reason hit-rate shows its own "of N": a rate whose denominator is
 * invisible cannot be checked. */
export function Stat({
  label,
  value,
  note,
}: {
  label: string
  value: string
  note?: string
}) {
  return (
    <div className="min-w-28 rounded-lg border border-line bg-card px-4 py-3 shadow-sm">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-2xl font-semibold tabular-nums text-ink">{value}</div>
      {note && <div className="mt-0.5 text-[11px] text-amber-700">{note}</div>}
    </div>
  )
}

/** The hit-rate readout — the actual answer when a threshold is set, so it
 * leads with the percentage instead of hiding it in a small inline pill.
 *
 * `showThreshold` is a prop, not derived here: the boolean-metric sets differ
 * per entity (a team has btts/clean_sheet/carded, a player only carded), and
 * echoing a threshold on a boolean metric is the bug fixed in fed030d.
 */
export function HitRate({
  metricLabel, direction, threshold, hits, n, pct, showThreshold,
}: {
  metricLabel: string
  direction: string
  threshold: number
  hits: number
  n: number
  pct: number
  showThreshold: boolean
}) {
  return (
    <div className="mb-5 inline-flex items-center gap-4 rounded-xl border border-accent/30 bg-accent/10 px-5 py-3">
      <div className="text-3xl font-semibold tabular-nums text-accent-ink">{pct}%</div>
      <div className="text-sm leading-tight">
        <div className="text-ink">
          {metricLabel} {direction}
          {showThreshold && ` ${threshold}`}
        </div>
        <div className="text-accent-ink">
          {hits} of {n} games
        </div>
      </div>
    </div>
  )
}
