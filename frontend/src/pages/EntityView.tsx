import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type Entity, type SearchHit, type Summary } from '../api'
import { useCatalogue } from '../useCatalogue'

const label = (m: string) =>
  m.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

const DEFAULT_METRIC: Record<Entity, string> = {
  team: 'btts',
  player: 'shots_on_target',
}

const fmt = (v: number | null | undefined, dp = 2) =>
  v === null || v === undefined ? '—' : Number.isInteger(v) ? String(v) : v.toFixed(dp)

export default function EntityView({ entity }: { entity: Entity }) {
  const { id } = useParams()
  const entityId = Number(id)
  const { competitions, metrics, error: catError } = useCatalogue()

  const metricList = useMemo(
    () => (metrics ? metrics[entity] : []),
    [metrics, entity],
  )

  // Controls
  const [metric, setMetric] = useState(DEFAULT_METRIC[entity])
  const [n, setN] = useState(10)
  const [competitionId, setCompetitionId] = useState<string>('') // '' = all
  const [windowMode, setWindowMode] = useState<'display' | 'going_in'>('display')
  const [threshold, setThreshold] = useState<string>('')
  const [direction, setDirection] = useState<'over' | 'under'>('over')

  const [summary, setSummary] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset metric to a valid default when the entity/catalogue changes.
  useEffect(() => {
    if (metricList.length && !metricList.includes(metric))
      setMetric(DEFAULT_METRIC[entity])
  }, [metricList, entity]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!Number.isFinite(entityId)) return
    const params: Record<string, string> = {
      metric,
      n: String(n),
      window_mode: windowMode,
    }
    if (competitionId) params.competition_id = competitionId
    if (threshold.trim() !== '') {
      params.threshold = threshold.trim()
      params.direction = direction
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .summary(entity, entityId, params)
      .then((s) => !cancelled && setSummary(s))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [entity, entityId, metric, n, competitionId, windowMode, threshold, direction])

  const ctrl =
    'rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 outline-none focus:border-sky-600'

  // Compose the scope subtitle client-side (the API's window string surfaces the
  // raw competition id; we have the name in the catalogue).
  const compName = competitionId
    ? competitions.find((c) => String(c.id) === competitionId)?.name
    : null
  const subtitle = `Last ${n} · ${windowMode === 'display' ? 'form' : 'going-in'} · ${
    compName ?? 'all competitions'
  }`

  return (
    <div>
      <div className="mb-1 flex items-baseline gap-3">
        <h1 className="text-2xl font-semibold text-slate-100">
          {summary?.entity_name ?? (loading ? '…' : `#${entityId}`)}
        </h1>
        <span className="text-sm text-slate-500 capitalize">{entity}</span>
      </div>

      {entity === 'team' && Number.isFinite(entityId) && (
        <OpponentPicker teamId={entityId} />
      )}

      {/* Controls */}
      <div className="mb-5 flex flex-wrap items-end gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
        <Field label="Metric">
          <select
            className={ctrl}
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
          >
            {metricList.map((m) => (
              <option key={m} value={m}>
                {label(m)}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Last N">
          <input
            type="number"
            min={1}
            max={100}
            value={n}
            onChange={(e) =>
              setN(Math.min(100, Math.max(1, Number(e.target.value) || 1)))
            }
            className={`${ctrl} w-20`}
          />
        </Field>

        <Field label="Competition">
          <select
            className={ctrl}
            value={competitionId}
            onChange={(e) => setCompetitionId(e.target.value)}
          >
            <option value="">All competitions</option>
            {competitions.map((c) => (
              <option key={c.id} value={String(c.id)}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Window">
          <select
            className={ctrl}
            value={windowMode}
            onChange={(e) =>
              setWindowMode(e.target.value as 'display' | 'going_in')
            }
          >
            <option value="display">Form (incl. latest)</option>
            <option value="going_in">Going-in (excl. latest)</option>
          </select>
        </Field>

        <Field label="Threshold (hit-rate)">
          <div className="flex gap-1">
            <input
              type="number"
              step="0.5"
              placeholder="—"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              className={`${ctrl} w-20`}
            />
            <select
              className={ctrl}
              value={direction}
              onChange={(e) => setDirection(e.target.value as 'over' | 'under')}
            >
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
        <SummaryBody
          summary={summary}
          entity={entity}
          subtitle={subtitle}
          dim={loading}
        />
      )}
      {loading && !summary && <p className="text-slate-500">Loading…</p>}
    </div>
  )
}

function OpponentPicker({ teamId }: { teamId: number }) {
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    if (q.trim().length < 2) {
      setHits([])
      return
    }
    timer.current = setTimeout(() => {
      api
        .search(q.trim())
        .then((r) => setHits(r.filter((h) => h.entity === 'team' && h.id !== teamId)))
        .catch(() => setHits([]))
    }, 200)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [q, teamId])

  return (
    <div className="relative mb-5 max-w-sm">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Compare head-to-head vs…"
        className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-100 outline-none focus:border-sky-600"
      />
      {hits.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full overflow-hidden rounded-md border border-slate-700 bg-slate-900 shadow-lg">
          {hits.slice(0, 8).map((h) => (
            <li key={h.id}>
              <button
                onMouseDown={() => navigate(`/fixture/${teamId}/vs/${h.id}`)}
                className="block w-full px-3 py-1.5 text-left text-sm text-slate-200 hover:bg-sky-800"
              >
                {h.name}
              </button>
            </li>
          ))}
        </ul>
      )}
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

function SummaryBody({
  summary: s,
  entity,
  subtitle,
  dim,
}: {
  summary: Summary
  entity: Entity
  subtitle: string
  dim: boolean
}) {
  const isPlayer = entity === 'player'
  return (
    <div className={dim ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
      <p className="mb-3 text-sm text-slate-500">{subtitle}</p>

      {s.games === 0 ? (
        <p className="rounded-md border border-slate-800 bg-slate-900/40 px-3 py-4 text-slate-400">
          No games in this scope.
        </p>
      ) : (
        <>
          <div className="mb-2 flex flex-wrap gap-3">
            <Stat label="Games" value={String(s.games)} />
            <Stat label="Total" value={fmt(s.total)} />
            <Stat
              label={isPlayer ? 'Per appearance' : 'Average'}
              value={fmt(isPlayer ? s.per_appearance : s.average)}
            />
            {isPlayer && <Stat label="Per 90" value={fmt(s.per_90)} />}
            {isPlayer && (
              <Stat label="Minutes" value={fmt(s.minutes_total, 0)} />
            )}
          </div>

          {s.hit_rate && (
            <div className="mb-5 inline-flex items-center gap-2 rounded-lg border border-sky-800 bg-sky-950/40 px-4 py-2">
              <span className="text-sm text-sky-300">
                {label(s.metric)} {s.hit_rate.direction} {s.hit_rate.threshold}
              </span>
              <span className="text-lg font-semibold text-slate-100">
                {s.hit_rate.hits} of {s.hit_rate.n}
              </span>
              <span className="text-sm text-sky-400">({s.hit_rate.pct}%)</span>
            </div>
          )}

          <Breakdown summary={s} isPlayer={isPlayer} />
        </>
      )}
    </div>
  )
}

function Breakdown({ summary: s, isPlayer }: { summary: Summary; isPlayer: boolean }) {
  // Chronological oldest→newest from the API; show newest first for reading.
  const rows = [...s.breakdown].reverse()
  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr className="border-b border-slate-800 text-left text-slate-500">
          <th className="py-2 pr-3 font-normal">Date</th>
          <th className="py-2 pr-3 font-normal">Opponent</th>
          <th className="py-2 pr-3 font-normal">H/A</th>
          {isPlayer && <th className="py-2 pr-3 text-right font-normal">Min</th>}
          <th className="py-2 pr-3 text-right font-normal">{label(s.metric)}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-slate-900 hover:bg-slate-900/40">
            <td className="py-1.5 pr-3 text-slate-400">
              {new Date(r.date).toLocaleDateString('en-GB')}
            </td>
            <td className="py-1.5 pr-3 text-slate-200">{r.opponent ?? '—'}</td>
            <td className="py-1.5 pr-3 text-slate-500">
              {r.is_home ? 'H' : 'A'}
            </td>
            {isPlayer && (
              <td className="py-1.5 pr-3 text-right text-slate-400">
                {fmt(r.minutes, 0)}
              </td>
            )}
            <td className="py-1.5 pr-3 text-right font-medium text-slate-100">
              {fmt(r.value)}
            </td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr className="text-slate-300">
          <td className="py-2 pr-3 font-medium" colSpan={isPlayer ? 4 : 3}>
            Total ({s.games} games)
          </td>
          <td className="py-2 pr-3 text-right font-semibold">{fmt(s.total)}</td>
        </tr>
      </tfoot>
    </table>
  )
}
