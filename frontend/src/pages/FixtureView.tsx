import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { api, type FixtureComparison, type FixtureRow } from '../api'
import { summarise, type MetricKind } from '../lib/aggregate'
import { useCatalogue } from '../useCatalogue'
import { SquadSection } from './SquadForm'
import { LastNInput } from '../components/LastNInput'

type Venue = 'recent' | 'home' | 'away'
type Mode = 'form' | 'h2h' | 'squad'
type Scope = 'league' | 'league_cups' | 'cups'

// competition_type values sent to /fixtures/compare per selector state
const SCOPE_PARAMS: Record<Scope, string[]> = {
  league: ['club_league'],
  league_cups: ['club_league', 'club_cup'],
  cups: ['club_cup'],
}

const SCOPE_LABELS: Record<Scope, string> = {
  league: 'league',
  league_cups: 'league + cups',
  cups: 'cups',
}

interface MetricDef {
  label: string
  kind: MetricKind
  get: (r: FixtureRow) => number | boolean | null
}

// The comparison table, computed client-side from raw rows (docs/adr/0005).
const METRICS: MetricDef[] = [
  { label: 'Goals for', kind: 'count', get: (r) => r.gf },
  { label: 'Goals against', kind: 'count', get: (r) => r.ga },
  { label: 'Total goals', kind: 'count', get: (r) => r.total_goals },
  { label: 'BTTS', kind: 'bool', get: (r) => r.btts },
  { label: 'Clean sheet', kind: 'bool', get: (r) => r.clean_sheet },
  { label: 'Shots on target', kind: 'count', get: (r) => r.sot },
  { label: 'Shots', kind: 'count', get: (r) => r.shots },
  { label: 'Corners', kind: 'count', get: (r) => r.corners },
  { label: 'Fouls', kind: 'count', get: (r) => r.fouls },
  { label: 'Yellow cards', kind: 'count', get: (r) => r.yellows },
]

const byVenue = (rows: FixtureRow[], v: Venue) =>
  v === 'home' ? rows.filter((r) => r.is_home)
    : v === 'away' ? rows.filter((r) => !r.is_home)
    : rows

const aggLabel = (rows: FixtureRow[], m: MetricDef) => {
  const a = summarise(rows.map(m.get), { kind: m.kind })
  if (a.average === null) return '—'
  return m.kind === 'bool' ? `${Math.round(a.average * 100)}%` : a.average.toFixed(2)
}

const resultClass = (r: string | null) =>
  r === 'W' ? 'bg-emerald-900/60 text-emerald-300'
    : r === 'L' ? 'bg-rose-900/60 text-rose-300'
    : 'bg-slate-700/60 text-slate-300'

const date = (d: string) => new Date(d).toLocaleDateString('en-GB')

export default function FixtureView() {
  const { homeId, awayId } = useParams()
  const home = Number(homeId)
  const away = Number(awayId)

  const { metrics } = useCatalogue()
  const [mode, setMode] = useState<Mode>('form')
  const [n, setN] = useState(10)
  const [scope, setScope] = useState<Scope>('league')
  const [venueHome, setVenueHome] = useState<Venue>('home')
  const [venueAway, setVenueAway] = useState<Venue>('away')
  const [h2hVenue, setH2hVenue] = useState<Venue>('recent')
  const [h2hComp, setH2hComp] = useState<string>('all')

  const [data, setData] = useState<FixtureComparison | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!Number.isFinite(home) || !Number.isFinite(away)) return
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .fixturesCompare(home, away, n, SCOPE_PARAMS[scope])
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [home, away, n, scope])

  // Pair the H2H rows into meetings (host first), most-recent-first.
  const meetings = useMemo(() => {
    if (!data) return []
    const byFixture = new Map<number, FixtureRow[]>()
    for (const r of data.h2h) {
      const list = byFixture.get(r.fixture_id) ?? []
      list.push(r)
      byFixture.set(r.fixture_id, list)
    }
    const out = [...byFixture.values()]
      .map((rows) => {
        const a = rows.find((r) => r.team_id === data.home_id)!
        const b = rows.find((r) => r.team_id === data.away_id)!
        const host = a.is_home ? a : b
        const guest = a.is_home ? b : a
        return { fixture_id: a.fixture_id, date: a.date, competition: a.competition, a, b, host, guest }
      })
      .sort((x, y) => +new Date(y.date) - +new Date(x.date))
    const byComp = h2hComp === 'all' ? out : out.filter((m) => m.competition === h2hComp)
    return h2hVenue === 'home' ? byComp.filter((m) => m.a.is_home)
      : h2hVenue === 'away' ? byComp.filter((m) => !m.a.is_home)
      : byComp
  }, [data, h2hVenue, h2hComp])

  // competitions present across ALL meetings (chips stay stable while filtering)
  const h2hComps = useMemo(() => {
    if (!data) return []
    return [...new Set(data.h2h.map((r) => r.competition))].sort()
  }, [data])

  if (error)
    return (
      <div className="rounded-md border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300">
        {error}
      </div>
    )
  if (!data) return <p className="text-slate-500">Loading…</p>

  const homeRows = byVenue(data.home, venueHome)
  const awayRows = byVenue(data.away, venueAway)
  const hWins = meetings.filter((m) => m.a.result === 'W').length
  const draws = meetings.filter((m) => m.a.result === 'D').length
  const aWins = meetings.filter((m) => m.a.result === 'L').length

  return (
    <div className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
      <h1 className="mb-1 text-2xl font-semibold text-slate-100">
        {data.home_name} <span className="text-slate-500">vs</span> {data.away_name}
      </h1>
      <p className="mb-4 text-sm text-slate-500">
        {data.home_name} (home) vs {data.away_name} (away) · form: {SCOPE_LABELS[scope]} ·
        H2H: all meetings incl. cups
      </p>

      {/* Mode + window */}
      <div className="mb-5 flex flex-wrap items-end gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
        <Field label="View">
          <Toggle
            value={mode}
            onChange={(v) => setMode(v as Mode)}
            options={[
              ['form', 'Team form'],
              ['h2h', 'Head-to-Head'],
              ['squad', 'Squad form'],
            ]}
          />
        </Field>
        {mode !== 'squad' && (
          <Field label="Last N">
            <LastNInput n={n} setN={setN} max={50} />
          </Field>
        )}
        {mode === 'form' && (
          <Field label="Competitions">
            <Toggle
              value={scope}
              onChange={(v) => setScope(v as Scope)}
              options={[
                ['league', 'League'],
                ['league_cups', 'League + Cups'],
                ['cups', 'Cups'],
              ]}
            />
          </Field>
        )}
      </div>

      {mode === 'form' ? (
        <FormMode
          data={data}
          homeRows={homeRows}
          awayRows={awayRows}
          venueHome={venueHome}
          venueAway={venueAway}
          setVenueHome={setVenueHome}
          setVenueAway={setVenueAway}
        />
      ) : mode === 'h2h' ? (
        <H2HMode
          data={data}
          meetings={meetings}
          h2hVenue={h2hVenue}
          setH2hVenue={setH2hVenue}
          h2hComp={h2hComp}
          setH2hComp={setH2hComp}
          comps={h2hComps}
          record={{ hWins, draws, aWins }}
        />
      ) : (
        <SquadSection
          homeId={data.home_id}
          awayId={data.away_id}
          metricList={metrics?.player ?? []}
        />
      )}
    </div>
  )
}

function FormMode({
  data,
  homeRows,
  awayRows,
  venueHome,
  venueAway,
  setVenueHome,
  setVenueAway,
}: {
  data: FixtureComparison
  homeRows: FixtureRow[]
  awayRows: FixtureRow[]
  venueHome: Venue
  venueAway: Venue
  setVenueHome: (v: Venue) => void
  setVenueAway: (v: Venue) => void
}) {
  return (
    <>
      {/* Per-team venue toggles */}
      <div className="mb-4 grid grid-cols-2 gap-4">
        <VenuePanel name={data.home_name} value={venueHome} onChange={setVenueHome} count={homeRows.length} />
        <VenuePanel name={data.away_name} value={venueAway} onChange={setVenueAway} count={awayRows.length} />
      </div>

      {/* Comparison table */}
      <table className="mb-6 w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-slate-500">
            <th className="py-2 text-left font-normal">Metric</th>
            <th className="py-2 text-right font-normal">{data.home_name}</th>
            <th className="py-2 text-right font-normal">{data.away_name}</th>
          </tr>
        </thead>
        <tbody>
          {METRICS.map((m) => (
            <tr key={m.label} className="border-b border-slate-900">
              <td className="py-1.5 text-slate-300">{m.label}</td>
              <td className="py-1.5 text-right font-medium text-slate-100">{aggLabel(homeRows, m)}</td>
              <td className="py-1.5 text-right font-medium text-slate-100">{aggLabel(awayRows, m)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <FixtureList title={`${data.home_name} — recent`} rows={homeRows} />
        <FixtureList title={`${data.away_name} — recent`} rows={awayRows} />
      </div>
    </>
  )
}

function H2HMode({
  data,
  meetings,
  h2hVenue,
  setH2hVenue,
  h2hComp,
  setH2hComp,
  comps,
  record,
}: {
  data: FixtureComparison
  meetings: Array<{ fixture_id: number; date: string; competition: string; a: FixtureRow; b: FixtureRow; host: FixtureRow; guest: FixtureRow }>
  h2hVenue: Venue
  setH2hVenue: (v: Venue) => void
  h2hComp: string
  setH2hComp: (v: string) => void
  comps: string[]
  record: { hWins: number; draws: number; aWins: number }
}) {
  if (data.h2h.length === 0)
    return (
      <p className="rounded-md border border-slate-800 bg-slate-900/40 px-3 py-4 text-slate-400">
        No meetings on record between {data.home_name} and {data.away_name} in the covered seasons.
      </p>
    )
  const aMeetingRows = meetings.map((m) => m.a)
  const bMeetingRows = meetings.map((m) => m.b)
  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-3">
          <Record label={`${data.home_name} wins`} value={record.hWins} tone="emerald" />
          <Record label="Draws" value={record.draws} tone="slate" />
          <Record label={`${data.away_name} wins`} value={record.aWins} tone="rose" />
        </div>
        <div className="flex items-end gap-3">
          {comps.length > 1 && (
            <Field label="Competition">
              <Toggle
                value={h2hComp}
                onChange={setH2hComp}
                options={[['all', 'All'], ...comps.map((c): [string, string] => [c, c])]}
              />
            </Field>
          )}
          <Field label={`${data.home_name} venue`}>
            <Toggle
              value={h2hVenue}
              onChange={(v) => setH2hVenue(v as Venue)}
              options={[
                ['recent', 'All'],
                ['home', 'Home'],
                ['away', 'Away'],
              ]}
            />
          </Field>
        </div>
      </div>

      {/* Aggregate over meetings */}
      <table className="mb-6 w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-slate-500">
            <th className="py-2 text-left font-normal">Over {meetings.length} meetings</th>
            <th className="py-2 text-right font-normal">{data.home_name}</th>
            <th className="py-2 text-right font-normal">{data.away_name}</th>
          </tr>
        </thead>
        <tbody>
          {METRICS.map((m) => (
            <tr key={m.label} className="border-b border-slate-900">
              <td className="py-1.5 text-slate-300">{m.label}</td>
              <td className="py-1.5 text-right font-medium text-slate-100">{aggLabel(aMeetingRows, m)}</td>
              <td className="py-1.5 text-right font-medium text-slate-100">{aggLabel(bMeetingRows, m)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="mb-2 text-sm text-slate-500">Meetings</h3>
      <div className="divide-y divide-slate-900">
        {meetings.map((m) => (
          <MeetingRow key={m.fixture_id} m={m} />
        ))}
      </div>
    </>
  )
}

function MeetingRow({ m }: { m: { fixture_id: number; date: string; competition: string; host: FixtureRow; guest: FixtureRow; a: FixtureRow } }) {
  const [open, setOpen] = useState(false)
  const hostName = m.guest.opponent
  const guestName = m.host.opponent
  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-3 py-2 text-left hover:bg-slate-900/40"
      >
        <span className="w-24 shrink-0 text-xs text-slate-500">{date(m.date)}</span>
        <span className="w-28 shrink-0 text-xs text-slate-600">{m.competition}</span>
        <span className="flex-1 text-right text-slate-200">{hostName}</span>
        <span className="rounded bg-slate-800 px-2 py-0.5 font-semibold text-slate-100">
          {m.host.gf}–{m.host.ga}
        </span>
        <span className="flex-1 text-slate-200">{guestName}</span>
      </button>
      {open && <DrillDown fixtureId={m.fixture_id} />}
    </div>
  )
}

function FixtureList({ title, rows }: { title: string; rows: FixtureRow[] }) {
  // label rows only when the window mixes competitions (cup scopes, relegation seasons)
  const showComp = new Set(rows.map((r) => r.competition)).size > 1
  return (
    <div>
      <h3 className="mb-2 text-sm text-slate-500">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-slate-600">No games in this venue filter.</p>
      ) : (
        <div className="divide-y divide-slate-900">
          {rows.map((r) => (
            <FixtureRowItem key={r.fixture_id} r={r} showComp={showComp} />
          ))}
        </div>
      )}
    </div>
  )
}

function FixtureRowItem({ r, showComp }: { r: FixtureRow; showComp?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 py-2 text-left text-sm hover:bg-slate-900/40"
      >
        <span className="w-20 shrink-0 text-xs text-slate-500">{date(r.date)}</span>
        <span className={`grid h-5 w-5 shrink-0 place-items-center rounded text-xs font-bold ${resultClass(r.result)}`}>
          {r.result}
        </span>
        <span className="w-5 shrink-0 text-xs text-slate-600">{r.is_home ? 'H' : 'A'}</span>
        <span className="flex-1 truncate text-slate-200">{r.opponent}</span>
        {showComp && (
          <span className="max-w-28 shrink-0 truncate text-xs text-slate-600">{r.competition}</span>
        )}
        <span className="font-medium text-slate-100">{r.gf}–{r.ga}</span>
      </button>
      {open && <DrillDown fixtureId={r.fixture_id} />}
    </div>
  )
}

function DrillDown({ fixtureId }: { fixtureId: number }) {
  const [rows, setRows] = useState<FixtureRow[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    api
      .fixtureDetail(fixtureId)
      .then((d) => !cancelled && setRows(d))
      .catch((e) => !cancelled && setErr(String(e.message ?? e)))
    return () => {
      cancelled = true
    }
  }, [fixtureId])

  if (err) return <p className="px-3 py-2 text-xs text-rose-400">{err}</p>
  if (!rows) return <p className="px-3 py-2 text-xs text-slate-500">Loading…</p>
  const host = rows.find((r) => r.is_home) ?? rows[0]
  const guest = rows.find((r) => !r.is_home) ?? rows[1]
  const lines: Array<[string, number | string | null, number | string | null]> = [
    ['Goals', host.gf, guest.gf],
    ['Shots', host.shots, guest.shots],
    ['On target', host.sot, guest.sot],
    ['Corners', host.corners, guest.corners],
    ['Fouls', host.fouls, guest.fouls],
    ['Yellows', host.yellows, guest.yellows],
    ['Reds', host.reds, guest.reds],
  ]
  return (
    <div className="mb-2 ml-4 rounded-md border border-slate-800 bg-slate-900/60 p-3 text-sm">
      <div className="mb-1 flex justify-between text-xs text-slate-400">
        <span>{guest.opponent}</span>
        <span>{host.opponent}</span>
      </div>
      {lines.map(([label, h, g]) => (
        <div key={label} className="flex items-center justify-between border-t border-slate-800/60 py-1">
          <span className="w-10 text-right font-medium text-slate-100">{h ?? '—'}</span>
          <span className="text-xs text-slate-500">{label}</span>
          <span className="w-10 font-medium text-slate-100">{g ?? '—'}</span>
        </div>
      ))}
    </div>
  )
}

function VenuePanel({ name, value, onChange, count }: { name: string; value: Venue; onChange: (v: Venue) => void; count: number }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="font-medium text-slate-200">{name}</span>
        <span className="text-xs text-slate-500">{count} games</span>
      </div>
      <Toggle
        value={value}
        onChange={(v) => onChange(v as Venue)}
        options={[
          ['recent', 'Recent'],
          ['home', 'Home'],
          ['away', 'Away'],
        ]}
      />
    </div>
  )
}

function Toggle({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: Array<[string, string]> }) {
  return (
    <div className="inline-flex overflow-hidden rounded-md border border-slate-700">
      {options.map(([v, label]) => (
        <button
          key={v}
          onClick={() => onChange(v)}
          className={`px-3 py-1.5 text-sm ${
            value === v ? 'bg-sky-700 text-white' : 'bg-slate-900 text-slate-300 hover:bg-slate-800'
          }`}
        >
          {label}
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

function Record({ label, value, tone }: { label: string; value: number; tone: 'emerald' | 'slate' | 'rose' }) {
  const tones = {
    emerald: 'border-emerald-800 bg-emerald-950/40 text-emerald-300',
    slate: 'border-slate-700 bg-slate-900/40 text-slate-300',
    rose: 'border-rose-800 bg-rose-950/40 text-rose-300',
  }
  return (
    <div className={`rounded-lg border px-4 py-2 text-center ${tones[tone]}`}>
      <div className="text-xl font-semibold">{value}</div>
      <div className="text-xs opacity-80">{label}</div>
    </div>
  )
}
