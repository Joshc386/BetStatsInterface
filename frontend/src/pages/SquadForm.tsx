// Squad-form panel — a club's Recent squad (players whose most-recent appearance
// was for that club) with each member's headline Summary Metric, computed
// client-side from raw rows (docs/adr/0006). One reusable panel, mounted on the
// Fixture view (both teams, shared controls) and the Team hub (one team).
//
// Membership and the "ghost" caveat are appearance-derived: a member lingers if
// his last game was here, so the panel is always labelled appearance-based.

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api, type SquadAppearanceRow, type SquadForm as SquadFormData } from '../api'
import { summarise, type MetricKind } from '../lib/aggregate'

const label = (m: string) =>
  m.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
const fmtDate = (d: string) => new Date(d).toLocaleDateString('en-GB')

const ctrl =
  'rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 outline-none focus:border-sky-600'

interface MetricDef {
  kind: MetricKind
  get: (r: SquadAppearanceRow) => number | boolean | null
}

// Keyed by the backend player-metric registry name (see app/stats.py PLAYER_METRICS).
const PLAYER_METRICS: Record<string, MetricDef> = {
  goals: { kind: 'count', get: (r) => r.goals },
  assists: { kind: 'count', get: (r) => r.assists },
  shots: { kind: 'count', get: (r) => r.shots },
  shots_on_target: { kind: 'count', get: (r) => r.sot },
  tackles: { kind: 'count', get: (r) => r.tackles },
  fouls_drawn: { kind: 'count', get: (r) => r.fouls_drawn },
  fouls_committed: { kind: 'count', get: (r) => r.fouls_committed },
  yellows: { kind: 'count', get: (r) => r.yellows },
  reds: { kind: 'count', get: (r) => r.reds },
  minutes: { kind: 'count', get: (r) => r.minutes },
  carded: { kind: 'bool', get: (r) => r.carded },
}

// 'all' clears the scope filter (membership is scope-independent; only the figure
// is scoped). Default League keeps the headline scope-clean.
const SCOPES: Array<[string, string]> = [
  ['club_league', 'League'],
  ['club_cup', 'Cups'],
  ['international', 'International'],
  ['all', 'All'],
]

export interface SquadControlState {
  metric: string
  setMetric: (v: string) => void
  n: number
  setN: (v: number) => void
  scope: string
  setScope: (v: string) => void
  threshold: string
  setThreshold: (v: string) => void
  direction: 'over' | 'under'
  setDirection: (v: 'over' | 'under') => void
}

/** Shared control state for the panel(s) on one surface. On the Fixture view a
 * single instance drives both squads so they stay comparable. */
export function useSquadControls(): SquadControlState {
  const [metric, setMetric] = useState('shots_on_target')
  const [n, setN] = useState(6)
  const [scope, setScope] = useState('club_league')
  const [threshold, setThreshold] = useState('')
  const [direction, setDirection] = useState<'over' | 'under'>('over')
  return {
    metric, setMetric, n, setN, scope, setScope,
    threshold, setThreshold, direction, setDirection,
  }
}

export function SquadControls({
  state: c,
  metricList,
}: {
  state: SquadControlState
  metricList: string[]
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
      <Field label="Metric">
        <select className={ctrl} value={c.metric} onChange={(e) => c.setMetric(e.target.value)}>
          {metricList.map((m) => (
            <option key={m} value={m}>{label(m)}</option>
          ))}
        </select>
      </Field>
      <Field label="Last N">
        <input
          type="number" min={1} max={30} value={c.n}
          onChange={(e) => c.setN(Math.min(30, Math.max(1, Number(e.target.value) || 1)))}
          className={`${ctrl} w-20`}
        />
      </Field>
      <Field label="Scope">
        <Toggle value={c.scope} onChange={c.setScope} options={SCOPES} />
      </Field>
      <Field label="Threshold (hit-rate)">
        <div className="flex gap-1">
          <input
            type="number" step="0.5" placeholder="—" value={c.threshold}
            onChange={(e) => c.setThreshold(e.target.value)} className={`${ctrl} w-20`}
          />
          <select
            className={ctrl} value={c.direction}
            onChange={(e) => c.setDirection(e.target.value as 'over' | 'under')}
          >
            <option value="over">over</option>
            <option value="under">under</option>
          </select>
        </div>
      </Field>
    </div>
  )
}

interface Computed {
  player_id: number
  player: string
  last_seen: string
  rows: SquadAppearanceRow[] // the window (last N at this club, in scope), newest-first
  figure: string
  apps: number
  sortKey: number
}

/** One club's Squad-form panel: fetches its raw rows once, then computes each
 * member's headline figure from the shared controls — no refetch on metric / N /
 * scope / threshold change (only teamId refetches). */
export function SquadFormPanel({
  teamId,
  state: c,
}: {
  teamId: number
  state: SquadControlState
}) {
  const [data, setData] = useState<SquadFormData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!Number.isFinite(teamId)) return
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .squadForm(teamId)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [teamId])

  const computed = useMemo<Computed[]>(() => {
    if (!data) return []
    const def = PLAYER_METRICS[c.metric] ?? PLAYER_METRICS.shots_on_target
    const threshold = c.threshold.trim() !== '' ? Number(c.threshold) : undefined

    // Rows arrive grouped by player, newest-first (backend ORDER BY player, date desc).
    const byPlayer = new Map<number, SquadAppearanceRow[]>()
    for (const r of data.rows) {
      if (c.scope !== 'all' && r.competition_type !== c.scope) continue
      const list = byPlayer.get(r.player_id) ?? []
      list.push(r)
      byPlayer.set(r.player_id, list)
    }

    const rows = data.members.map((m) => {
      const window = (byPlayer.get(m.player_id) ?? []).slice(0, c.n) // last N at this club, in scope
      const agg = summarise(window.map(def.get), {
        kind: def.kind, threshold, direction: c.direction,
      })
      return {
        player_id: m.player_id,
        player: m.player,
        last_seen: m.last_seen,
        rows: window,
        apps: agg.games,
        figure: figureText(agg, def.kind, threshold),
        // sort by the figure: hit-rate% when thresholded, else the per-app average.
        sortKey: threshold !== undefined ? (agg.pct ?? -1) : (agg.average ?? -Infinity),
      }
    })
    rows.sort((a, b) => b.sortKey - a.sortKey)
    return rows
  }, [data, c.metric, c.n, c.scope, c.threshold, c.direction])

  if (error)
    return (
      <div className="rounded-md border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300">
        {error}
      </div>
    )
  if (!data) return <p className="text-slate-500">Loading…</p>

  return (
    <div className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
      <div className="mb-1 flex items-baseline justify-between">
        <h3 className="font-medium text-slate-200">{data.team_name}</h3>
        <span className="text-xs text-slate-500">{data.members.length} in recent squad</span>
      </div>
      <p className="mb-2 text-xs text-slate-600">
        Based on recent appearances — not a registered roster. “Last seen” flags likely departures.
      </p>
      <div className="overflow-hidden rounded-lg border border-slate-800">
        {computed.map((p) => (
          <PlayerRow key={p.player_id} p={p} metric={c.metric} />
        ))}
      </div>
    </div>
  )
}

function PlayerRow({ p, metric }: { p: Computed; metric: string }) {
  const [open, setOpen] = useState(false)
  const def = PLAYER_METRICS[metric] ?? PLAYER_METRICS.shots_on_target
  return (
    <div className="border-b border-slate-900 last:border-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-slate-900/50"
      >
        <span className="w-4 shrink-0 text-xs text-slate-600">{open ? '▾' : '▸'}</span>
        <span className="flex-1 truncate text-slate-200">{p.player}</span>
        <span className="w-24 shrink-0 text-right text-xs text-slate-500">{fmtDate(p.last_seen)}</span>
        <span className="w-14 shrink-0 text-right text-xs text-slate-500">{p.apps} app{p.apps === 1 ? '' : 's'}</span>
        <span className="w-20 shrink-0 text-right font-semibold text-slate-100">{p.figure}</span>
      </button>
      {open && <MiniBreakdown rows={p.rows} def={def} metric={metric} playerId={p.player_id} />}
    </div>
  )
}

function MiniBreakdown({
  rows, def, metric, playerId,
}: {
  rows: SquadAppearanceRow[]
  def: MetricDef
  metric: string
  playerId: number
}) {
  const cell = (v: number | boolean | null) =>
    v === null || v === undefined ? '—'
      : typeof v === 'boolean' ? (v ? '✓' : '·')
      : String(v)
  return (
    <div className="bg-slate-950/40 px-3 pb-2">
      {rows.length === 0 ? (
        <p className="py-2 text-xs text-slate-600">No appearances in this scope.</p>
      ) : (
        <table className="w-full border-collapse text-xs">
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-slate-900/60 last:border-0">
                <td className="py-1 pr-3 text-slate-500">{fmtDate(r.date)}</td>
                <td className="py-1 pr-3 text-slate-300">{r.opponent}</td>
                <td className="py-1 pr-3 text-slate-600">{r.is_home ? 'H' : 'A'}</td>
                <td className="py-1 pr-3 text-right text-slate-500">{r.minutes ?? '—'}′</td>
                <td className="py-1 text-right font-medium text-slate-200">{cell(def.get(r))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Link
        to={`/player/${playerId}`}
        className="mt-1 inline-block text-xs text-sky-400 hover:text-sky-300"
      >
        full player view → ({label(metric)} spells, all metrics)
      </Link>
    </div>
  )
}

/** Two squads side by side with shared controls — the Fixture view's Squad-form. */
export function SquadSection({
  homeId,
  awayId,
  metricList,
}: {
  homeId: number
  awayId: number
  metricList: string[]
}) {
  const state = useSquadControls()
  return (
    <>
      <SquadControls state={state} metricList={metricList} />
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <SquadFormPanel teamId={homeId} state={state} />
        <SquadFormPanel teamId={awayId} state={state} />
      </div>
    </>
  )
}

/** One squad with its own controls — the Team hub's Squad-form. */
export function SingleSquad({ teamId, metricList }: { teamId: number; metricList: string[] }) {
  const state = useSquadControls()
  return (
    <>
      <SquadControls state={state} metricList={metricList} />
      <SquadFormPanel teamId={teamId} state={state} />
    </>
  )
}

function figureText(
  agg: ReturnType<typeof summarise>,
  kind: MetricKind,
  threshold: number | undefined,
): string {
  if (threshold !== undefined) {
    if (agg.pct === null) return '—'
    return `${agg.hits}/${agg.n} · ${agg.pct}%`
  }
  if (agg.average === null) return '—'
  return kind === 'bool' ? `${Math.round(agg.average * 100)}%` : agg.average.toFixed(2)
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
