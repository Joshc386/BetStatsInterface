import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type Entity, type SearchHit, type Summary, type UpcomingFixture } from '../api'
import { useCatalogue } from '../useCatalogue'
import { SingleSquad } from './SquadForm'
import { LastNInput } from '../components/LastNInput'
import { EntityLink, teamHref } from '../components/EntityLink'
import { ResultChip, ValueBar, barFraction } from '../components/ResultChip'
import {
  ControlBar, ControlGroup, Field, HitRate, Stat, Toggle, ctrl, sampleNote,
} from '../components/controls'

const label = (m: string) =>
  m.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

// Boolean-kind metrics (app/stats.py's registry) — their hit-rate threshold
// number is ignored server-side, so don't echo a stale one in the label.
const BOOL_METRICS = new Set(['btts', 'clean_sheet', 'carded'])

const DEFAULT_METRIC: Record<Entity, string> = {
  team: 'btts',
  player: 'shots_on_target',
}

const fmt = (v: number | null | undefined, dp = 2) =>
  v === null || v === undefined ? '—' : Number.isInteger(v) ? String(v) : v.toFixed(dp)

// season tag "2526" -> "2025-26"
const seasonFmt = (s: string) => (s.length === 4 ? `20${s.slice(0, 2)}-${s.slice(2)}` : s)

export default function EntityView({ entity }: { entity: Entity }) {
  const { id } = useParams()
  const entityId = Number(id)
  const { competitions, metrics, seasons: seasonCat, error: catError } = useCatalogue()

  const metricList = useMemo(
    () => (metrics ? metrics[entity] : []),
    [metrics, entity],
  )
  const entitySeasons = useMemo(
    () => (seasonCat ? seasonCat[entity] : []),
    [seasonCat, entity],
  ) // newest first

  // Controls
  const [metric, setMetric] = useState(DEFAULT_METRIC[entity])
  const [winMode, setWinMode] = useState<'games' | 'seasons'>('games')
  const [n, setN] = useState(10)
  const [seasonCount, setSeasonCount] = useState(1) // most-recent N seasons
  const [venue, setVenue] = useState('all') // 'all' | 'home' | 'away'
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

  // season mode: the most-recent `seasonCount` seasons; games mode: undefined
  const selectedSeasons = useMemo(
    () => (winMode === 'seasons' ? entitySeasons.slice(0, seasonCount) : undefined),
    [winMode, entitySeasons, seasonCount],
  )

  useEffect(() => {
    if (!Number.isFinite(entityId)) return
    const params: Record<string, string> = { metric }
    if (winMode === 'games') {
      params.n = String(n) // both ignored by the API in season mode
      params.window_mode = windowMode
    }
    if (venue !== 'all') params.is_home = venue === 'home' ? 'true' : 'false'
    if (competitionId) params.competition_id = competitionId
    if (threshold.trim() !== '') {
      params.threshold = threshold.trim()
      params.direction = direction
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .summary(entity, entityId, params, selectedSeasons)
      .then((s) => !cancelled && setSummary(s))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [entity, entityId, metric, winMode, n, selectedSeasons, venue, competitionId, windowMode, threshold, direction])


  // Compose the scope subtitle client-side (the API's window string surfaces the
  // raw competition id; we have the name in the catalogue).
  const compName = competitionId
    ? competitions.find((c) => String(c.id) === competitionId)?.name
    : null
  const windowLabel =
    winMode === 'games'
      ? `Last ${n} · ${windowMode === 'display' ? 'form' : 'going-in'}`
      : seasonCount === 1
        ? seasonFmt(entitySeasons[0] ?? '')
        : `Last ${seasonCount} seasons`
  const subtitle = `${windowLabel}${venue !== 'all' ? ` · ${venue === 'home' ? 'Home' : 'Away'}` : ''} · ${
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
        <>
          <NextFixtures teamId={entityId} />
          <OpponentPicker teamId={entityId} />
        </>
      )}

      {/* Controls — grouped what / window / filters / hit-rate */}
      <ControlBar>
        <ControlGroup>
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
        </ControlGroup>

        <ControlGroup>
          <Field label="Window">
            <Toggle
              value={winMode}
              onChange={(v) => setWinMode(v as 'games' | 'seasons')}
              options={[['games', 'Games'], ['seasons', 'Seasons']]}
            />
          </Field>

        {winMode === 'games' ? (
          <>
            <Field label="Last N">
              <LastNInput n={n} setN={setN} max={100} />
            </Field>
            <Field label="Basis">
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
          </>
        ) : (
          <Field label="Seasons">
            <select
              className={ctrl}
              value={seasonCount}
              onChange={(e) => setSeasonCount(Number(e.target.value))}
            >
              {entitySeasons.map((_, i) => (
                <option key={i} value={i + 1}>
                  {i === 0 ? `This season (${seasonFmt(entitySeasons[0])})` : `Last ${i + 1} seasons`}
                </option>
              ))}
            </select>
          </Field>
        )}
        </ControlGroup>

        <ControlGroup>
          <Field label="Venue">
            <Toggle
              value={venue}
              onChange={setVenue}
              options={[['all', 'All'], ['home', 'Home'], ['away', 'Away']]}
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
        </ControlGroup>

        <ControlGroup>
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
        </ControlGroup>
      </ControlBar>

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

      {entity === 'team' && Number.isFinite(entityId) && (
        <section className="mt-10">
          <h2 className="mb-3 text-lg font-semibold text-slate-200">Squad form</h2>
          <SingleSquad teamId={entityId} metricList={metrics?.player ?? []} />
        </section>
      )}
    </div>
  )
}

/** This club's actual next fixtures, as links into the Fixture view — the thing
 * you nearly always want from a team page, previously only reachable by typing
 * the opponent's name into the picker below. Client-side filter of the shared
 * upcoming feed; no extra endpoint. */
function NextFixtures({ teamId }: { teamId: number }) {
  const [fixtures, setFixtures] = useState<UpcomingFixture[] | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .fixturesUpcoming(60)
      .then(
        (d) =>
          !cancelled &&
          setFixtures(
            d
              .filter((f) => f.home_id === teamId || f.away_id === teamId)
              .slice(0, 3),
          ),
      )
      .catch(() => !cancelled && setFixtures([])) // a bonus panel must never break the page
    return () => {
      cancelled = true
    }
  }, [teamId])

  if (!fixtures || fixtures.length === 0) return null

  return (
    <div className="mb-4">
      <h2 className="mb-1 text-xs uppercase tracking-wide text-slate-500">
        Next fixtures
      </h2>
      <div className="flex flex-wrap gap-2">
        {fixtures.map((f) => {
          const isHome = f.home_id === teamId
          return (
            <Link
              key={f.fixture_id}
              to={`/fixture/${f.home_id}/vs/${f.away_id}`}
              className="rounded-md border border-slate-800 bg-slate-900/40 px-3 py-1.5 text-sm hover:border-sky-800 hover:bg-slate-900"
            >
              <span className="text-slate-500">
                {new Date(f.date).toLocaleDateString('en-GB', {
                  weekday: 'short',
                  day: 'numeric',
                  month: 'short',
                })}
              </span>{' '}
              <span className="text-slate-600">{isHome ? 'v' : '@'}</span>{' '}
              <span className="text-slate-100">
                {isHome ? f.away_name : f.home_name}
              </span>
            </Link>
          )
        })}
      </div>
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
        placeholder="or compare head-to-head vs any team…"
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
              note={sampleNote(s.recorded_games, s.games, 'games')}
            />
            {isPlayer && (
              <Stat label="Per 90" value={fmt(s.per_90)} note={sampleNote(s.recorded_minutes, s.minutes_total, 'mins')} />
            )}
            {isPlayer && (
              <Stat label="Minutes" value={fmt(s.minutes_total, 0)} />
            )}
          </div>

          {s.hit_rate && (
            <HitRate
              metricLabel={label(s.metric)}
              direction={s.hit_rate.direction}
              threshold={s.hit_rate.threshold}
              hits={s.hit_rate.hits}
              n={s.hit_rate.n}
              pct={s.hit_rate.pct}
              showThreshold={!BOOL_METRICS.has(s.metric)}
            />
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
  // Bars are scaled to this window, not to some absolute — the question is
  // "which of these games stand out", not "how does this compare to all football".
  const max = Math.max(0, ...rows.map((r) => r.value ?? 0))
  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr className="border-b border-slate-800 text-left text-slate-500">
          <th className="py-2 pr-3 font-normal">Date</th>
          <th className="w-8 py-2 pr-3 font-normal" />
          <th className="py-2 pr-3 font-normal">Opponent</th>
          <th className="py-2 pr-3 font-normal">H/A</th>
          {isPlayer && <th className="py-2 pr-3 text-right font-normal">Min</th>}
          <th className="py-2 pr-3 text-right font-normal" colSpan={2}>
            {label(s.metric)}
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-slate-900 hover:bg-slate-900/40">
            <td className="py-1.5 pr-3 text-slate-400">
              {new Date(r.date).toLocaleDateString('en-GB')}
            </td>
            <td className="py-1.5 pr-3">
              <ResultChip result={r.result} />
            </td>
            <td className="py-1.5 pr-3 text-slate-200">
              <EntityLink to={teamHref(r.opponent_id)}>{r.opponent ?? '—'}</EntityLink>
            </td>
            <td className="py-1.5 pr-3 text-slate-500">
              {r.is_home ? 'H' : 'A'}
            </td>
            {isPlayer && (
              <td className="py-1.5 pr-3 text-right text-slate-400">
                {fmt(r.minutes, 0)}
              </td>
            )}
            <td className="w-24 py-1.5 pr-3">
              <ValueBar fraction={barFraction(r.value, max)} />
            </td>
            <td className="w-14 py-1.5 pr-3 text-right font-medium text-slate-100">
              {fmt(r.value)}
            </td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr className="text-slate-300">
          <td className="py-2 pr-3 font-medium" colSpan={isPlayer ? 6 : 5}>
            Total ({s.games} games)
          </td>
          <td className="py-2 pr-3 text-right font-semibold">{fmt(s.total)}</td>
        </tr>
      </tfoot>
    </table>
  )
}
