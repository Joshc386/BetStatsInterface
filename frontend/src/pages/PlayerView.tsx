import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { api, type BreakdownRow, type Summary } from '../api'
import { useCatalogue } from '../useCatalogue'

const label = (m: string) =>
  m.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

const fmt = (v: number | null | undefined, dp = 2) =>
  v === null || v === undefined ? '—' : Number.isInteger(v) ? String(v) : v.toFixed(dp)

const date = (d: string) => new Date(d).toLocaleDateString('en-GB')

// competition_type -> selector label. 'all' clears the scope filter.
const SCOPES: Array<[string, string]> = [
  ['club_league', 'League'],
  ['club_cup', 'Cups'],
  ['international', 'International'],
  ['all', 'All'],
]

type Segment = 'team' | 'competition' | 'none'

const ctrl =
  'rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 outline-none focus:border-sky-600'

interface Group {
  key: number
  label: string
  rows: BreakdownRow[]
  games: number
  total: number
}

/** Group the window's appearances by club or competition, newest group first.
 * Subtotals are a plain sum of the same rows, so they reconcile to the headline. */
function groupBreakdown(rows: BreakdownRow[], by: Segment): Group[] | null {
  if (by === 'none') return null
  const newestFirst = [...rows].reverse() // API breakdown is oldest->newest
  const order: number[] = []
  const map = new Map<number, Group>()
  for (const r of newestFirst) {
    const k = (by === 'team' ? r.team_id : r.competition_id) ?? -1
    if (!map.has(k)) {
      map.set(k, {
        key: k,
        label: (by === 'team' ? r.team : r.competition) ?? '—',
        rows: [],
        games: 0,
        total: 0,
      })
      order.push(k)
    }
    const g = map.get(k)!
    g.rows.push(r)
    g.games += 1
    g.total += r.value == null ? 0 : Number(r.value)
  }
  return order.map((k) => map.get(k)!)
}

export default function PlayerView() {
  const { id } = useParams()
  const playerId = Number(id)
  const { metrics, error: catError } = useCatalogue()
  const metricList = useMemo(() => (metrics ? metrics.player : []), [metrics])

  const [metric, setMetric] = useState('shots_on_target')
  const [n, setN] = useState(10)
  const [scope, setScope] = useState('club_league') // default: league (scope-clean)
  const [segment, setSegment] = useState<Segment>('team')
  const [threshold, setThreshold] = useState('')
  const [direction, setDirection] = useState<'over' | 'under'>('over')
  // drill-in filters set by clicking a segment
  const [teamFilter, setTeamFilter] = useState<{ id: number; name: string } | null>(null)
  const [compFilter, setCompFilter] = useState<{ id: number; name: string } | null>(null)

  const [summary, setSummary] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (metricList.length && !metricList.includes(metric)) setMetric('shots_on_target')
  }, [metricList]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!Number.isFinite(playerId)) return
    const params: Record<string, string> = { metric, n: String(n) }
    if (scope !== 'all') params.scope = scope
    if (teamFilter) params.team_id = String(teamFilter.id)
    if (compFilter) params.competition_id = String(compFilter.id)
    if (threshold.trim() !== '') {
      params.threshold = threshold.trim()
      params.direction = direction
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .summary('player', playerId, params)
      .then((s) => !cancelled && setSummary(s))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [playerId, metric, n, scope, teamFilter, compFilter, threshold, direction])

  const groups = useMemo(
    () => (summary ? groupBreakdown(summary.breakdown, segment) : null),
    [summary, segment],
  )

  const scopeName = SCOPES.find(([v]) => v === scope)?.[1] ?? 'League'

  return (
    <div>
      <div className="mb-1 flex items-baseline gap-3">
        <h1 className="text-2xl font-semibold text-slate-100">
          {summary?.entity_name ?? (loading ? '…' : `#${playerId}`)}
        </h1>
        <span className="text-sm text-slate-500">player</span>
      </div>

      {/* active drill-in filters */}
      {(teamFilter || compFilter) && (
        <div className="mb-3 flex flex-wrap gap-2">
          {teamFilter && (
            <Chip label={`Club: ${teamFilter.name}`} onClear={() => setTeamFilter(null)} />
          )}
          {compFilter && (
            <Chip label={`Competition: ${compFilter.name}`} onClear={() => setCompFilter(null)} />
          )}
        </div>
      )}

      {/* controls */}
      <div className="mb-5 flex flex-wrap items-end gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
        <Field label="Metric">
          <select className={ctrl} value={metric} onChange={(e) => setMetric(e.target.value)}>
            {metricList.map((m) => (
              <option key={m} value={m}>{label(m)}</option>
            ))}
          </select>
        </Field>
        <Field label="Last N">
          <input
            type="number" min={1} max={100} value={n}
            onChange={(e) => setN(Math.min(100, Math.max(1, Number(e.target.value) || 1)))}
            className={`${ctrl} w-20`}
          />
        </Field>
        <Field label="Scope">
          <Toggle value={scope} onChange={setScope} options={SCOPES} />
        </Field>
        <Field label="Segment by">
          <Toggle
            value={segment}
            onChange={(v) => setSegment(v as Segment)}
            options={[['team', 'Team'], ['competition', 'Competition'], ['none', 'None']]}
          />
        </Field>
        <Field label="Threshold (hit-rate)">
          <div className="flex gap-1">
            <input
              type="number" step="0.5" placeholder="—" value={threshold}
              onChange={(e) => setThreshold(e.target.value)} className={`${ctrl} w-20`}
            />
            <select className={ctrl} value={direction} onChange={(e) => setDirection(e.target.value as 'over' | 'under')}>
              <option value="over">over</option>
              <option value="under">under</option>
            </select>
          </div>
        </Field>
      </div>

      {(error || catError) && (
        <div className="mb-4 rounded-md border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300">
          {error ?? catError}
        </div>
      )}

      {summary && (
        <div className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
          <p className="mb-3 text-sm text-slate-500">
            Last {n} · {scopeName}
            {teamFilter ? ` · ${teamFilter.name}` : ''}
            {compFilter ? ` · ${compFilter.name}` : ''}
          </p>

          {summary.games === 0 ? (
            <p className="rounded-md border border-slate-800 bg-slate-900/40 px-3 py-4 text-slate-400">
              No appearances in this scope.
            </p>
          ) : (
            <>
              <div className="mb-2 flex flex-wrap gap-3">
                <Stat label="Apps" value={String(summary.games)} />
                <Stat label="Total" value={fmt(summary.total)} />
                <Stat label="Per app" value={fmt(summary.per_appearance)} />
                <Stat label="Per 90" value={fmt(summary.per_90)} />
                <Stat label="Minutes" value={fmt(summary.minutes_total, 0)} />
              </div>

              {summary.hit_rate && (
                <div className="mb-5 inline-flex items-center gap-2 rounded-lg border border-sky-800 bg-sky-950/40 px-4 py-2">
                  <span className="text-sm text-sky-300">
                    {label(summary.metric)} {summary.hit_rate.direction} {summary.hit_rate.threshold}
                  </span>
                  <span className="text-lg font-semibold text-slate-100">
                    {summary.hit_rate.hits} of {summary.hit_rate.n}
                  </span>
                  <span className="text-sm text-sky-400">({summary.hit_rate.pct}%)</span>
                </div>
              )}

              {groups ? (
                <GroupedBreakdown
                  groups={groups}
                  metric={summary.metric}
                  segment={segment}
                  onPick={(g) =>
                    segment === 'team'
                      ? setTeamFilter({ id: g.key, name: g.label })
                      : setCompFilter({ id: g.key, name: g.label })
                  }
                />
              ) : (
                <FlatBreakdown rows={summary.breakdown} />
              )}
            </>
          )}
        </div>
      )}
      {loading && !summary && <p className="text-slate-500">Loading…</p>}
    </div>
  )
}

function GroupedBreakdown({
  groups, metric, segment, onPick,
}: {
  groups: Group[]
  metric: string
  segment: Segment
  onPick: (g: Group) => void
}) {
  return (
    <div className="space-y-4">
      {groups.map((g) => (
        <div key={g.key} className="overflow-hidden rounded-lg border border-slate-800">
          <button
            onClick={() => onPick(g)}
            title={`Filter to ${g.label}`}
            className="flex w-full items-center justify-between bg-slate-900/70 px-3 py-2 text-left hover:bg-slate-800"
          >
            <span className="font-medium text-slate-100">{g.label}</span>
            <span className="text-sm text-slate-400">
              {label(metric)} <span className="font-semibold text-slate-200">{fmt(g.total)}</span> · {g.games} {g.games === 1 ? 'app' : 'apps'}
              <span className="ml-2 text-xs text-sky-400">filter ↓</span>
            </span>
          </button>
          <FlatBreakdown rows={g.rows} alreadyOrdered showComp={segment === 'team'} />
        </div>
      ))}
    </div>
  )
}

function FlatBreakdown({
  rows, alreadyOrdered = false, showComp = false,
}: {
  rows: BreakdownRow[]
  alreadyOrdered?: boolean
  showComp?: boolean
}) {
  const ordered = alreadyOrdered ? rows : [...rows].reverse()
  return (
    <table className="w-full border-collapse text-sm">
      <tbody>
        {ordered.map((r, i) => (
          <tr key={i} className="border-b border-slate-900 last:border-0 hover:bg-slate-900/40">
            <td className="py-1.5 pl-3 pr-3 text-slate-400">{date(r.date)}</td>
            <td className="py-1.5 pr-3 text-slate-200">{r.opponent ?? '—'}</td>
            <td className="py-1.5 pr-3 text-slate-500">{r.is_home ? 'H' : 'A'}</td>
            {showComp && <td className="py-1.5 pr-3 text-xs text-slate-600">{r.competition ?? ''}</td>}
            <td className="py-1.5 pr-3 text-right text-slate-400">{fmt(r.minutes, 0)}′</td>
            <td className="py-1.5 pr-3 text-right font-medium text-slate-100">{fmt(r.value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Chip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-sky-800 bg-sky-950/50 px-3 py-1 text-sm text-sky-200">
      {label}
      <button onClick={onClear} className="text-sky-400 hover:text-sky-200" title="clear filter">✕</button>
    </span>
  )
}

function Toggle({
  value, onChange, options,
}: {
  value: string
  onChange: (v: string) => void
  options: Array<[string, string]>
}) {
  return (
    <div className="inline-flex overflow-hidden rounded-md border border-slate-700">
      {options.map(([v, text]) => (
        <button
          key={v}
          onClick={() => onChange(v)}
          className={`px-3 py-1.5 text-sm ${
            value === v ? 'bg-sky-700 text-white' : 'bg-slate-900 text-slate-300 hover:bg-slate-800'
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-slate-500">{label}</span>
      {children}
    </label>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-xl font-semibold text-slate-100">{value}</div>
    </div>
  )
}
