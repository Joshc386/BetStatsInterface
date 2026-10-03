import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  REFEREE_METRICS, api, type FixtureComparison, type FixtureRow, type RefereeOut,
  type RefereeSummary,
} from '../api'
import { summarise, type MetricKind } from '../lib/aggregate'
import { useCatalogue } from '../useCatalogue'
import { SquadSection } from './SquadForm'
import { LastNInput } from '../components/LastNInput'
import { resultClass } from '../components/ResultChip'
import { ControlBar, ControlGroup, Field, Toggle } from '../components/controls'
import { Hero, KitShirt } from '../components/Kit'
import { EntityLink, refereeHref } from '../components/EntityLink'
import {
  REFEREE_KIT, awayTheme, kitOf, refereeTheme, teamTheme, themeStyle, type Theme,
} from '../lib/teamTheme'

type Venue = 'recent' | 'home' | 'away'
type Mode = 'form' | 'h2h' | 'squad'
type Scope = 'league' | 'league_cups' | 'all' | 'cups' | 'europe'

// competition_type values sent to /fixtures/compare per selector state
const SCOPE_PARAMS: Record<Scope, string[]> = {
  league: ['club_league'],
  league_cups: ['club_league', 'club_cup'],
  all: ['club_league', 'club_cup', 'club_european'],
  cups: ['club_cup'],
  europe: ['club_european'],
}

const SCOPE_LABELS: Record<Scope, string> = {
  league: 'league',
  league_cups: 'league + cups',
  all: 'all competitions',
  cups: 'cups',
  europe: 'Europe',
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

const date = (d: string) => new Date(d).toLocaleDateString('en-GB')

// Each side in its own colours; the away side changes kit on a clash.
const sideThemes = (d: FixtureComparison) => {
  const home = teamTheme(d.home_id)
  return { home, away: awayTheme(home, d.away_id) }
}

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
      <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
        {error}
      </div>
    )
  if (!data) return <p className="text-muted">Loading…</p>

  const homeRows = byVenue(data.home, venueHome)
  const awayRows = byVenue(data.away, venueAway)
  const hWins = meetings.filter((m) => m.a.result === 'W').length
  const draws = meetings.filter((m) => m.a.result === 'D').length
  const aWins = meetings.filter((m) => m.a.result === 'L').length
  const themes = sideThemes(data)

  return (
    <div className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
      <div className="relative mb-2 grid overflow-hidden rounded-xl shadow-sm sm:grid-cols-2">
        <Hero
          theme={themes.home}
          title={<Link to={`/team/${data.home_id}`} className="hover:underline">{data.home_name}</Link>}
          subtitle="Home"
        />
        <Hero
          theme={themes.away}
          mirror
          title={<Link to={`/team/${data.away_id}`} className="hover:underline">{data.away_name}</Link>}
          subtitle="Away"
        />
        <span className="absolute left-1/2 top-1/2 hidden -translate-x-1/2 -translate-y-1/2 rounded-full bg-card px-2.5 py-1 text-xs font-bold text-ink shadow sm:block">
          vs
        </span>
      </div>
      <p className="mb-4 text-sm text-muted">
        {data.home_name} (home) vs {data.away_name} (away) · form: {SCOPE_LABELS[scope]} ·
        H2H: all meetings, every competition
      </p>

      <AppointedReferee />

      {/* Mode + window */}
      <ControlBar>
        <ControlGroup>
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
        </ControlGroup>
        <ControlGroup>
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
                ['all', 'All comps'],
                ['cups', 'Cups'],
                ['europe', 'Europe'],
              ]}
            />
          </Field>
        )}
        </ControlGroup>
      </ControlBar>

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
  const themes = sideThemes(data)
  return (
    <>
      {/* Per-team venue toggles */}
      <div className="mb-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <VenuePanel theme={themes.home} name={data.home_name} value={venueHome} onChange={setVenueHome} count={homeRows.length} />
        <VenuePanel theme={themes.away} name={data.away_name} value={venueAway} onChange={setVenueAway} count={awayRows.length} />
      </div>

      {/* Comparison table */}
      <table className="mb-6 w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-line text-muted">
            <th className="py-2 text-left font-normal">Metric</th>
            <SideHeader theme={themes.home} name={data.home_name} />
            <SideHeader theme={themes.away} name={data.away_name} />
          </tr>
        </thead>
        <tbody>
          {METRICS.map((m) => (
            <tr key={m.label} className="border-b border-line-soft">
              <td className="py-1.5 text-ink-2">{m.label}</td>
              <td className="py-1.5 text-right font-medium text-ink">{aggLabel(homeRows, m)}</td>
              <td className="py-1.5 text-right font-medium text-ink">{aggLabel(awayRows, m)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <FixtureList
          theme={themes.home}
          title={`${data.home_name} — ${venueTitle(venueHome)}`}
          rows={homeRows}
          scopeEmpty={data.home.length === 0}
        />
        <FixtureList
          theme={themes.away}
          title={`${data.away_name} — ${venueTitle(venueAway)}`}
          rows={awayRows}
          scopeEmpty={data.away.length === 0}
        />
      </div>
    </>
  )
}

const venueTitle = (v: Venue) =>
  v === 'home' ? 'home games' : v === 'away' ? 'away games' : 'recent'

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
      <p className="rounded-md border border-line bg-card px-3 py-4 text-muted">
        No meetings on record between {data.home_name} and {data.away_name} in the covered seasons.
      </p>
    )
  const themes = sideThemes(data)
  const aMeetingRows = meetings.map((m) => m.a)
  const bMeetingRows = meetings.map((m) => m.b)
  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-3">
          <Record label={`${data.home_name} wins`} value={record.hWins} theme={themes.home} />
          <Record label="Draws" value={record.draws} />
          <Record label={`${data.away_name} wins`} value={record.aWins} theme={themes.away} />
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
          <tr className="border-b border-line text-muted">
            <th className="py-2 text-left font-normal">Over {meetings.length} meetings</th>
            <SideHeader theme={themes.home} name={data.home_name} />
            <SideHeader theme={themes.away} name={data.away_name} />
          </tr>
        </thead>
        <tbody>
          {METRICS.map((m) => (
            <tr key={m.label} className="border-b border-line-soft">
              <td className="py-1.5 text-ink-2">{m.label}</td>
              <td className="py-1.5 text-right font-medium text-ink">{aggLabel(aMeetingRows, m)}</td>
              <td className="py-1.5 text-right font-medium text-ink">{aggLabel(bMeetingRows, m)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="mb-2 text-sm text-muted">Meetings</h3>
      <div className="divide-y divide-line-soft">
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
        className="flex w-full items-center gap-3 py-2 text-left hover:bg-sunken"
      >
        <span className="w-24 shrink-0 text-xs text-muted">{date(m.date)}</span>
        <span className="w-28 shrink-0 text-xs text-faint">{m.competition}</span>
        <span className="flex flex-1 items-center justify-end gap-2 text-ink">
          {hostName}
          <KitShirt kit={kitOf(m.host.team_id)} />
        </span>
        <span className="rounded bg-ink px-2 py-0.5 font-semibold tabular-nums text-card">
          {m.host.gf}–{m.host.ga}
        </span>
        <span className="flex flex-1 items-center gap-2 text-ink">
          <KitShirt kit={kitOf(m.guest.team_id)} />
          {guestName}
        </span>
      </button>
      {open && <DrillDown fixtureId={m.fixture_id} />}
    </div>
  )
}

function FixtureList({
  theme,
  title,
  rows,
  scopeEmpty,
}: {
  theme: Theme
  title: string
  rows: FixtureRow[]
  scopeEmpty?: boolean
}) {
  // label rows only when the window mixes competitions (cup scopes, relegation seasons)
  const showComp = new Set(rows.map((r) => r.competition)).size > 1
  // a sparse-coverage window can reach back years — say so rather than imply recent form
  const seasonSpan = new Set(rows.map((r) => r.season)).size
  return (
    <div style={themeStyle(theme)}>
      <h3 className="mb-2 flex items-center gap-2 text-sm text-muted">
        <KitShirt kit={theme.kit} />
        {title}
        {seasonSpan > 1 && (
          <span className="ml-2 text-xs text-faint">spans {seasonSpan} seasons</span>
        )}
      </h3>
      {rows.length === 0 ? (
        <p className="text-faint">
          {scopeEmpty
            ? 'No games in this competition scope.'
            : 'No games in this venue filter.'}
        </p>
      ) : (
        <div className="divide-y divide-line-soft">
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
        className="flex w-full items-center gap-2 py-2 text-left text-sm hover:bg-sunken"
      >
        <span className="w-20 shrink-0 text-xs text-muted">{date(r.date)}</span>
        <span className={`grid h-5 w-5 shrink-0 place-items-center rounded text-xs font-bold ${resultClass(r.result)}`}>
          {r.result}
        </span>
        <span className="w-5 shrink-0 text-xs text-faint">{r.is_home ? 'H' : 'A'}</span>
        <span className="flex-1 truncate text-ink">{r.opponent}</span>
        {showComp && (
          <span className="max-w-28 shrink-0 truncate text-xs text-faint">{r.competition}</span>
        )}
        <span className="font-medium text-ink">{r.gf}–{r.ga}</span>
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

  if (err) return <p className="px-3 py-2 text-xs text-rose-700">{err}</p>
  if (!rows) return <p className="px-3 py-2 text-xs text-muted">Loading…</p>
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
    <div className="mb-2 ml-4 rounded-md border border-line bg-sunken p-3 text-sm">
      {host.referee && (
        <div className="mb-2 flex items-center gap-1.5 text-xs text-muted">
          <KitShirt kit={REFEREE_KIT} className="h-3.5 w-3.5" />
          Referee:
          <EntityLink to={refereeHref(host.referee_id)} className="font-medium text-ink">
            {host.referee}
          </EntityLink>
        </div>
      )}
      <div className="mb-1 flex justify-between text-xs text-muted">
        <span>{guest.opponent}</span>
        <span>{host.opponent}</span>
      </div>
      {lines.map(([label, h, g]) => (
        <div key={label} className="flex items-center justify-between border-t border-line py-1">
          <span className="w-10 text-right font-medium text-ink">{h ?? '—'}</span>
          <span className="text-xs text-muted">{label}</span>
          <span className="w-10 font-medium text-ink">{g ?? '—'}</span>
        </div>
      ))}
    </div>
  )
}

/** The Appointed referee: picked by hand (appointments are confirmed about a
 * day before kick-off and no source we use knows them sooner), held in the page
 * address as ?ref= and never stored (CONTEXT.md "Appointed referee"). Shows his
 * League record over a Last-N window; his name opens the full Referee hub. */
function AppointedReferee() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('ref')
  const refId = raw !== null && Number.isFinite(Number(raw)) ? Number(raw) : null
  const [referees, setReferees] = useState<RefereeOut[] | null>(null)
  const [text, setText] = useState('')
  const [n, setN] = useState(10)
  const [summary, setSummary] = useState<RefereeSummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .referees()
      .then((r) => !cancelled && setReferees(r))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (refId === null) {
      setSummary(null)
      return
    }
    let cancelled = false
    setError(null)
    api
      .refereeSummary(refId, { n: String(n), scope: 'club_league' })
      .then((s) => !cancelled && setSummary(s))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
    return () => {
      cancelled = true
    }
  }, [refId, n])

  // the datalist hands back the full name; names are unique (uq_referees_name)
  const pick = (name: string) => {
    setText(name)
    const r = referees?.find((x) => x.name === name)
    if (!r) return
    // a fresh copy: mutating the router's own params object left the effect
    // below unfired after a pick (the render saw the change, React did not)
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('ref', String(r.id))
      return next
    }, { replace: true })
    setText('')
  }
  const clear = () =>
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('ref')
      return next
    }, { replace: true })

  return (
    <div style={themeStyle(refereeTheme())} className="mb-4 rounded-lg border border-line bg-card px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <KitShirt kit={REFEREE_KIT} className="h-6 w-6" />
        {summary ? (
          <div className="min-w-0">
            <div className="text-xs text-muted">Appointed referee</div>
            <Link
              to={`/referee/${summary.referee_id}`}
              className="font-semibold text-ink underline-offset-2 hover:underline"
            >
              {summary.name}
            </Link>
          </div>
        ) : (
          <span className="text-sm text-muted">Appointed referee</span>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <input
            list="referee-names"
            value={text}
            onChange={(e) => pick(e.target.value)}
            placeholder={refId === null ? 'Type the appointed referee’s name…' : 'Change referee…'}
            className="w-64 max-w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink outline-none focus:border-accent-ink"
          />
          <datalist id="referee-names">
            {referees?.map((r) => (
              <option key={r.id} value={r.name}>
                {r.matches} matches · last {date(r.last_date)}
              </option>
            ))}
          </datalist>
          {refId !== null && (
            <button onClick={clear} title="Clear the appointed referee" className="px-1 text-muted hover:text-ink">
              ✕
            </button>
          )}
        </div>
      </div>

      {error && <p className="mt-2 text-sm text-rose-700">{error}</p>}
      {refId === null && (
        <p className="mt-2 text-xs text-faint">
          Appointments are confirmed about a day before kick-off. Pick him here to see his record.
        </p>
      )}

      {summary && (
        <div className="mt-3 flex flex-wrap items-end gap-3 border-t border-line pt-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted">Last N (League)</span>
            <LastNInput n={n} setN={setN} max={100} />
          </label>
          {summary.matches === 0 ? (
            <p className="text-sm text-muted">
              No League matches recorded for him.{' '}
              <Link to={`/referee/${summary.referee_id}`} className="text-accent-ink underline">
                See every competition
              </Link>
            </p>
          ) : (
            <>
              <MiniStat label="Matches" value={String(summary.matches)} />
              {REFEREE_METRICS.map(([m, l]) => (
                <MiniStat key={m} label={`${l} / match`} value={summary.rates[m].per_match?.toFixed(2) ?? '—'} />
              ))}
              <Link
                to={`/referee/${summary.referee_id}`}
                className="ml-auto self-center text-sm text-accent-ink hover:underline"
              >
                Full record →
              </Link>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-sunken px-3 py-1.5">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="text-lg font-semibold tabular-nums text-ink">{value}</div>
    </div>
  )
}

function VenuePanel({ theme, name, value, onChange, count }: { theme: Theme; name: string; value: Venue; onChange: (v: Venue) => void; count: number }) {
  return (
    <div style={themeStyle(theme)} className="rounded-lg border border-line border-t-4 border-t-accent bg-card p-3">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="flex items-center gap-2 font-medium text-ink">
          <KitShirt kit={theme.kit} />
          {name}
        </span>
        <span className="text-xs text-muted">{count} games</span>
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



/** A side's win count in its own colours; draws (no theme) stay neutral. */
function Record({ label, value, theme }: { label: string; value: number; theme?: Theme }) {
  return (
    <div
      style={theme && themeStyle(theme)}
      className={`rounded-lg border px-4 py-2 text-center ${
        theme ? 'border-accent/30 bg-accent/10 text-accent-ink' : 'border-line bg-card text-ink-2'
      }`}
    >
      <div className="text-xl font-semibold">{value}</div>
      <div className="text-xs">{label}</div>
    </div>
  )
}

/** A comparison-table column head: the side's shirt and name. */
function SideHeader({ theme, name }: { theme: Theme; name: string }) {
  return (
    <th className="py-2 text-right font-normal">
      <span className="inline-flex items-center gap-1.5">
        <KitShirt kit={theme.kit} />
        {name}
      </span>
    </th>
  )
}
