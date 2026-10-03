import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type MatchPlayer, type MatchReport as Report, type MatchSide, type MatchTotals } from '../api'
import { Hero, KitShirt } from '../components/Kit'
import { EntityLink, playerHref, refereeHref, teamHref } from '../components/EntityLink'
import { REFEREE_KIT, awayTheme, refereeTheme, teamTheme, themeStyle, type Theme } from '../lib/teamTheme'

/** The Match report (CONTEXT.md): one finished Fixture. Every total except the
 * score and corners is summed from both squads' FBref player rows — the same
 * source as the Referee hub, so his figure for this game matches this page. */

const date = (d: string) =>
  new Date(d).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
const seasonFmt = (s: string) => (s.length === 4 ? `20${s.slice(0, 2)}-${s.slice(2)}` : s)
const num = (v: number | null) => (v === null ? '—' : String(v))

// The player table's columns, in order (decision 4 of the 03/10/2026 grill).
const COLUMNS: Array<[keyof MatchTotals, string, string]> = [
  ['goals', 'G', 'Goals'],
  ['assists', 'A', 'Assists'],
  ['shots', 'Sh', 'Shots'],
  ['sot', 'SoT', 'Shots on target'],
  ['tackles', 'TklW', 'Tackles won'],
  ['fouls_committed', 'Fls', 'Fouls committed'],
  ['fouls_drawn', 'Fld', 'Fouls drawn'],
  ['yellows', 'Y', 'Yellow cards'],
  ['reds', 'R', 'Red cards'],
]

// The side-by-side totals; corners alone comes from the team row.
const TOTAL_LINES: Array<[string, (s: MatchSide) => number | null]> = [
  ['Shots', (s) => s.totals.shots],
  ['On target', (s) => s.totals.sot],
  ['Corners', (s) => s.corners],
  ['Fouls', (s) => s.totals.fouls_committed],
  ['Yellows', (s) => s.totals.yellows],
  ['Reds', (s) => s.totals.reds],
  ['Cards', (s) => s.totals.cards],
]

export default function MatchReport() {
  const { id } = useParams()
  const fixtureId = Number(id)
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setReport(null)
    setError(null)
    api
      .matchReport(fixtureId)
      .then((r) => !cancelled && setReport(r))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
    return () => {
      cancelled = true
    }
  }, [fixtureId])

  if (error)
    return (
      <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
        {error}
      </div>
    )
  if (!report) return <p className="text-muted">Loading…</p>

  const { home, away } = report
  const homeTheme = teamTheme(home.team_id)
  const themes = { home: homeTheme, away: awayTheme(homeTheme, away.team_id) }
  const noPlayers = home.players.length === 0 && away.players.length === 0
  const sparse = [...home.players, ...away.players].some((p) => p.shots === null)

  return (
    <div>
      <div className="relative mb-2 grid overflow-hidden rounded-xl shadow-sm sm:grid-cols-2">
        <Hero
          theme={themes.home}
          title={<Link to={teamHref(home.team_id)!} className="hover:underline">{home.team}</Link>}
          subtitle="Home"
        />
        <Hero
          theme={themes.away}
          mirror
          title={<Link to={teamHref(away.team_id)!} className="hover:underline">{away.team}</Link>}
          subtitle="Away"
        />
        <span className="absolute left-1/2 top-1/2 hidden -translate-x-1/2 -translate-y-1/2 rounded-md bg-ink px-3 py-1 text-lg font-bold tabular-nums text-card shadow sm:block">
          {num(home.score)}–{num(away.score)}
        </span>
      </div>
      <p className="mb-4 text-sm text-muted">
        <span className="font-semibold text-ink sm:hidden">
          {num(home.score)}–{num(away.score)} ·{' '}
        </span>
        Match report · {date(report.date)} · {report.competition}
        {report.stage && ` · ${report.stage}`} · {seasonFmt(report.season)}
      </p>

      <RefereeCard report={report} awaiting={noPlayers} />

      {noPlayers && (
        <Note>
          No player data held for this match yet. FBref usually publishes it about a day after
          kick-off; the score and corners above come from the team rows.
        </Note>
      )}

      <Totals home={home} away={away} themes={themes} />

      {!noPlayers && (
        <>
          <div className="mb-3 space-y-1">
            {report.competition_type === 'club_league' && (
              <Note>
                Totals are summed from the player rows (FBref), the same source as the referee’s
                page. The team page follows football-data.co.uk and can differ, most often on a
                two-yellow sending-off (it records 1 red, 0 yellows).
              </Note>
            )}
            {sparse && (
              <Note>
                The source did not publish shots, tackles or fouls for this match. They show as —,
                never 0.
              </Note>
            )}
            {[home, away].map((s) => <GoalsGap key={s.team_id} side={s} />)}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <PlayerTable side={home} theme={themes.home} />
            <PlayerTable side={away} theme={themes.away} />
          </div>
        </>
      )}
    </div>
  )
}

function Note({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-faint">{children}</p>
}

/** The referee is read from the same FBref page as the player rows, so a game
 * still awaiting player data has not named him yet. */
function RefereeCard({ report, awaiting }: { report: Report; awaiting: boolean }) {
  return (
    <div
      style={themeStyle(refereeTheme())}
      className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-card px-4 py-3"
    >
      <KitShirt kit={REFEREE_KIT} className="h-6 w-6" />
      <div className="min-w-0">
        <div className="text-xs text-muted">Referee</div>
        {report.referee ? (
          <EntityLink to={refereeHref(report.referee_id)} className="font-semibold text-ink">
            {report.referee}
          </EntityLink>
        ) : (
          <span className="text-sm text-muted">
            {awaiting ? 'Named once the player data arrives' : 'Not recorded on the match page'}
          </span>
        )}
      </div>
      {report.referee_id !== null && (
        <Link
          to={refereeHref(report.referee_id)!}
          className="ml-auto text-sm text-accent-ink hover:underline"
        >
          Full record →
        </Link>
      )}
    </div>
  )
}

function Totals({ home, away, themes }: { home: MatchSide; away: MatchSide; themes: { home: Theme; away: Theme } }) {
  return (
    <div className="mb-3 rounded-lg border border-line bg-card px-4 py-3 text-sm">
      <div className="mb-1 flex justify-between text-xs text-muted">
        <span className="flex items-center gap-1.5"><KitShirt kit={themes.home.kit} />{home.team}</span>
        <span className="flex items-center gap-1.5">{away.team}<KitShirt kit={themes.away.kit} /></span>
      </div>
      {TOTAL_LINES.map(([label, get]) => (
        <div key={label} className="flex items-center justify-between border-t border-line-soft py-1">
          <span className="w-10 font-medium tabular-nums text-ink">{num(get(home))}</span>
          <span className="text-xs text-muted">{label}</span>
          <span className="w-10 text-right font-medium tabular-nums text-ink">{num(get(away))}</span>
        </div>
      ))}
    </div>
  )
}

/** Own goals are credited to no player, so player goals can trail the score.
 * Said out loud rather than leaving the footer and the score to disagree. */
function GoalsGap({ side }: { side: MatchSide }) {
  const { score, totals: { goals } } = side
  if (score === null || goals === null || score === goals) return null
  return (
    <Note>
      {side.team}: player goals {goals} against a score of {score} —{' '}
      {score > goals ? 'an own goal is credited to no player.' : 'the sources disagree.'}
    </Note>
  )
}

function PlayerTable({ side, theme }: { side: MatchSide; theme: Theme }) {
  const cell = 'px-1.5 py-1 text-right tabular-nums'
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-card">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-line text-muted">
            <th className="py-2 pl-3 pr-2 text-left font-normal">
              <span className="flex items-center gap-1.5 font-medium text-ink">
                <KitShirt kit={theme.kit} />
                {side.team}
              </span>
            </th>
            <th className={`${cell} font-normal`} title="Minutes">Min</th>
            {COLUMNS.map(([k, short, long]) => (
              <th key={k} className={`${cell} font-normal`} title={long}>{short}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {side.players.map((p) => (
            <PlayerRow key={p.player_id} p={p} cell={cell} />
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t border-line font-semibold text-ink">
            <td className="py-1.5 pl-3 pr-2">
              Total <span className="font-normal text-muted">· Cards {num(side.totals.cards)}</span>
            </td>
            <td className={cell} />
            {COLUMNS.map(([k]) => (
              <td key={k} className={cell}>{num(side.totals[k])}</td>
            ))}
          </tr>
        </tfoot>
      </table>
    </div>
  )
}

function PlayerRow({ p, cell }: { p: MatchPlayer; cell: string }) {
  const value = (v: number | null) => (
    <span className={v === 0 ? 'text-faint' : 'text-ink'}>{num(v)}</span>
  )
  return (
    <tr className="border-b border-line-soft last:border-0 hover:bg-sunken">
      <td className="max-w-40 truncate py-1 pl-3 pr-2 text-ink">
        <EntityLink to={playerHref(p.player_id)}>{p.player}</EntityLink>
      </td>
      <td className={`${cell} text-muted`}>{p.minutes}′</td>
      {COLUMNS.map(([k]) => (
        <td key={k} className={cell}>
          {value(p[k])}
          {k === 'reds' && p.second_yellow && (
            <sup className="ml-0.5 text-[0.6rem] font-semibold text-rose-700" title="Sent off for a second yellow: counts 2 Cards">
              2Y
            </sup>
          )}
        </td>
      ))}
    </tr>
  )
}
