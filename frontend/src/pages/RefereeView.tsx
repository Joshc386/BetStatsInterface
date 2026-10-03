import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  REFEREE_METRICS, api, type RefereeGame, type RefereeMetric, type RefereeSummary,
} from '../api'
import { useCatalogue } from '../useCatalogue'
import { LastNInput } from '../components/LastNInput'
import {
  ControlBar, ControlGroup, Field, HitRate, Stat, Toggle, ctrl, sampleNote,
} from '../components/controls'
import { EntityLink, matchHref, teamHref } from '../components/EntityLink'
import { Hero, KitShirt } from '../components/Kit'
import { kitOf, refereeTheme, themeStyle } from '../lib/teamTheme'

// competition_type -> selector label; League by default (CONTEXT.md "Referee")
const SCOPES: Array<[string, string]> = [
  ['club_league', 'League'],
  ['club_cup', 'Cups'],
  ['club_european', 'Europe'],
  ['international', 'International'],
  ['all', 'All'],
]

const fmt = (v: number | null | undefined, dp = 2) =>
  v === null || v === undefined ? '—' : Number.isInteger(v) ? String(v) : v.toFixed(dp)

const date = (d: string) => new Date(d).toLocaleDateString('en-GB')

// season tag "2526" -> "2025-26"
const seasonFmt = (s: string) => (s.length === 4 ? `20${s.slice(0, 2)}-${s.slice(2)}` : s)

export default function RefereeView() {
  const { id } = useParams()
  const refId = Number(id)
  const { seasons: seasonCat, error: catError } = useCatalogue()
  // his figures come from Player-Match rows, so the player season list applies
  const seasons = useMemo(() => seasonCat?.player ?? [], [seasonCat]) // newest first

  const [metric, setMetric] = useState<RefereeMetric>('cards')
  const [winMode, setWinMode] = useState<'games' | 'seasons'>('games')
  const [n, setN] = useState(10)
  const [seasonCount, setSeasonCount] = useState(1)
  const [scope, setScope] = useState('club_league')
  const scopeChosen = useRef(false) // a hand-picked scope is never overridden
  const [threshold, setThreshold] = useState('')
  const [direction, setDirection] = useState<'over' | 'under'>('over')

  const [summary, setSummary] = useState<RefereeSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // A new referee starts from the League default again.
  useEffect(() => {
    scopeChosen.current = false
    setScope('club_league')
  }, [refId])

  const selectedSeasons = useMemo(
    () => (winMode === 'seasons' ? seasons.slice(0, seasonCount) : undefined),
    [winMode, seasons, seasonCount],
  )

  useEffect(() => {
    if (!Number.isFinite(refId)) return
    const params: Record<string, string> = { metric, scope }
    if (winMode === 'games') params.n = String(n)
    if (threshold.trim() !== '') {
      params.threshold = threshold.trim()
      params.direction = direction
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .refereeSummary(refId, params, selectedSeasons)
      .then((s) => !cancelled && setSummary(s))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [refId, metric, winMode, n, selectedSeasons, scope, threshold, direction])

  // A referee we never see in the League (a UEFA referee in his covered ties,
  // an international-only one) opens on the scope that holds his matches.
  useEffect(() => {
    if (!summary || scopeChosen.current || summary.scope !== 'club_league') return
    if (summary.scope_counts.club_league) return
    const busiest = Object.entries(summary.scope_counts).sort((a, b) => b[1] - a[1])[0]
    if (busiest) setScope(busiest[0])
  }, [summary])

  const counts = summary?.scope_counts ?? {}
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  const scopeOptions: Array<[string, string]> = SCOPES.map(([v, l]) => {
    const c = v === 'all' ? total : counts[v]
    return [v, summary && c ? `${l} ${c}` : l]
  })
  const theme = refereeTheme()
  const windowLabel =
    winMode === 'games'
      ? `Last ${n}`
      : seasonCount === 1
        ? seasonFmt(seasons[0] ?? '')
        : `Last ${seasonCount} seasons`
  const metricLabel = REFEREE_METRICS.find(([m]) => m === metric)?.[1] ?? metric

  return (
    <div style={themeStyle(theme)}>
      <Hero
        theme={theme}
        className="mb-4 rounded-xl shadow-sm"
        title={summary?.name ?? (loading ? '…' : `#${refId}`)}
        subtitle={summary ? `Referee · ${total} matches recorded` : 'Referee'}
      />

      <ControlBar>
        <ControlGroup>
          <Field label="Metric">
            <Toggle
              value={metric}
              onChange={(v) => setMetric(v as RefereeMetric)}
              options={REFEREE_METRICS}
            />
          </Field>
        </ControlGroup>
        <ControlGroup>
          <Field label="Window">
            <Toggle
              value={winMode}
              onChange={(v) => setWinMode(v as 'games' | 'seasons')}
              options={[['games', 'Matches'], ['seasons', 'Seasons']]}
            />
          </Field>
          {winMode === 'games' ? (
            <Field label="Last N">
              <LastNInput n={n} setN={setN} max={100} />
            </Field>
          ) : (
            <Field label="Seasons">
              <select
                className={ctrl}
                value={seasonCount}
                onChange={(e) => setSeasonCount(Number(e.target.value))}
              >
                {seasons.map((_, i) => (
                  <option key={i} value={i + 1}>
                    {i === 0 ? `This season (${seasonFmt(seasons[0])})` : `Last ${i + 1} seasons`}
                  </option>
                ))}
              </select>
            </Field>
          )}
        </ControlGroup>
        <ControlGroup>
          <Field label="Scope">
            <Toggle
              value={scope}
              onChange={(v) => {
                scopeChosen.current = true
                setScope(v)
              }}
              options={scopeOptions}
            />
          </Field>
        </ControlGroup>
        <ControlGroup>
          <Field label="Threshold (hit-rate)">
            <div className="flex gap-1">
              <input
                type="number" step="0.5" placeholder="—" value={threshold}
                onChange={(e) => setThreshold(e.target.value)} className={`${ctrl} w-20`}
              />
              <select
                className={ctrl} value={direction}
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
        <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error ?? catError}
        </div>
      )}

      {summary && (
        <div className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
          <p className="mb-3 text-sm text-muted">
            {windowLabel} · {summary.scope_label} · match totals, both sides
          </p>

          {/* Europe only: internationals are ingested whole-competition (ADR 0011) */}
          {summary.covered_ties_only && ['club_european', 'all'].includes(summary.scope) && (
            <p className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              <strong>Covered ties only.</strong> UEFA appoints neutral referees, so we
              see him only in ties involving the English clubs we cover. This is not
              his full record.
            </p>
          )}

          {summary.matches === 0 ? (
            <p className="rounded-md border border-line bg-card px-3 py-4 text-muted">
              No matches refereed in this scope.
            </p>
          ) : (
            <>
              <div className="mb-2 flex flex-wrap gap-3">
                <Stat label="Matches" value={String(summary.matches)} />
                {REFEREE_METRICS.map(([m, l]) => (
                  <Stat
                    key={m}
                    label={`${l} / match`}
                    value={fmt(summary.rates[m].per_match)}
                    note={sampleNote(summary.rates[m].recorded, summary.matches, 'matches')}
                  />
                ))}
              </div>

              {summary.hit_rate && (
                <HitRate
                  metricLabel={metricLabel}
                  direction={summary.hit_rate.direction}
                  threshold={summary.hit_rate.threshold}
                  hits={summary.hit_rate.hits}
                  n={summary.hit_rate.n}
                  pct={summary.hit_rate.pct}
                  showThreshold
                />
              )}

              <SideSplit summary={summary} />
              <CountingNote />
              <GameList games={summary.games} metric={metric} />
            </>
          )}
        </div>
      )}
      {loading && !summary && <p className="text-muted">Loading…</p>}
    </div>
  )
}

/** Home side vs away side, per match, over the same recorded matches. */
function SideSplit({ summary }: { summary: RefereeSummary }) {
  return (
    <div className="mb-4 overflow-x-auto rounded-lg border border-line bg-card">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-xs text-muted">
            <th className="px-3 py-2 text-left font-normal">Per match</th>
            {REFEREE_METRICS.map(([m, l]) => (
              <th key={m} className="px-3 py-2 text-right font-normal">{l}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {([['Home side', summary.home_rates], ['Away side', summary.away_rates]] as const).map(
            ([label, rates]) => (
              <tr key={label} className="border-b border-line-soft last:border-0">
                <td className="px-3 py-1.5 text-ink">{label}</td>
                {REFEREE_METRICS.map(([m]) => (
                  <td key={m} className="px-3 py-1.5 text-right tabular-nums text-ink">
                    {fmt(rates[m])}
                  </td>
                ))}
              </tr>
            ),
          )}
        </tbody>
      </table>
    </div>
  )
}

/** Why a referee's yellows can differ from a team page's (ADR 0018). */
function CountingNote() {
  return (
    <p className="mb-4 text-xs leading-relaxed text-muted">
      Counted from FBref player rows. <strong>Cards</strong> = every yellow plus every
      red that did not follow a second yellow (at most 2 per player); Reds counts every
      sending-off. FBref records a two-yellow dismissal as 2 yellows + 1 red, while
      league team pages (football-data.co.uk) record it as 1 red, 0 yellows, so a
      league match can show two more yellows here per dismissal than on its team page.
    </p>
  )
}

function GameList({ games, metric }: { games: RefereeGame[]; metric: RefereeMetric }) {
  const newestFirst = [...games].reverse()
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-card">
      <table className="w-full min-w-[36rem] border-collapse text-sm">
        <thead>
          <tr className="border-b border-line text-xs text-muted">
            <th className="py-2 pl-3 pr-3 text-left font-normal">Date</th>
            <th className="py-2 pr-3 text-left font-normal">Match</th>
            <th className="py-2 pr-3 text-left font-normal">Competition</th>
            {REFEREE_METRICS.map(([m, l]) => (
              <th
                key={m}
                className={`py-2 pr-3 text-right font-normal ${m === metric ? 'text-ink' : ''}`}
              >
                {l}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {newestFirst.map((g) => (
            <GameRow key={g.fixture_id} g={g} metric={metric} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function GameRow({ g, metric }: { g: RefereeGame; metric: RefereeMetric }) {
  const [open, setOpen] = useState(false)
  const cell = (m: RefereeMetric, v: number | null) =>
    `py-1.5 pr-3 text-right tabular-nums ${
      v === null ? 'text-faint' : m === metric ? 'font-semibold text-ink' : 'text-muted'
    }`
  return (
    <>
      <tr
        onClick={() => setOpen((o) => !o)}
        className="cursor-pointer border-b border-line-soft hover:bg-sunken"
        title="Show each side's split"
      >
        <td className="py-1.5 pl-3 pr-3 whitespace-nowrap text-muted">{date(g.date)}</td>
        <td className="py-1.5 pr-3 text-ink">
          {/* the game opens its Match report; the rest of the row still expands */}
          <Link
            to={matchHref(g.fixture_id)!}
            onClick={(e) => e.stopPropagation()}
            title="Match report"
            className="inline-flex items-center gap-1.5 underline-offset-2 hover:text-accent-ink hover:underline"
          >
            <KitShirt kit={kitOf(g.home_id)} />
            {g.home}
            <span className="text-faint">v</span>
            <KitShirt kit={kitOf(g.away_id)} />
            {g.away}
          </Link>
        </td>
        <td className="py-1.5 pr-3 text-xs text-faint">{g.competition}</td>
        {REFEREE_METRICS.map(([m]) => (
          <td key={m} className={cell(m, g[m])}>{fmt(g[m])}</td>
        ))}
      </tr>
      {open && (
        <>
          {([[g.home_id, g.home, g.home_side], [g.away_id, g.away, g.away_side]] as const).map(
            ([teamId, name, side]) => (
              <tr key={teamId} className="border-b border-line-soft bg-sunken text-xs">
                <td />
                <td className="py-1 pr-3 pl-6 text-ink-2" colSpan={2}>
                  <EntityLink to={teamHref(teamId)}>{name}</EntityLink>
                </td>
                {REFEREE_METRICS.map(([m]) => (
                  <td key={m} className="py-1 pr-3 text-right tabular-nums text-ink-2">
                    {fmt(side[m])}
                  </td>
                ))}
              </tr>
            ),
          )}
        </>
      )}
    </>
  )
}
