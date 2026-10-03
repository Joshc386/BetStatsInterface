import { useEffect, useMemo, useState } from 'react'
import { api, type Competition, type LeaderClub, type LeaderMetric, type LeaderRow, type Leaderboard } from '../api'
import { KitShirt } from '../components/Kit'
import { EntityLink, matchHref, playerHref, teamHref } from '../components/EntityLink'
import { kitOf } from '../lib/teamTheme'

/** The Leaderboard (CONTEXT.md, ADR 0019): one league's top 10 per Metric, each
 * player over his club's last 10 League games with player data, per 90. */

const LISTS: Array<[LeaderMetric, string]> = [
  ['shots', 'Shots'],
  ['sot', 'Shots on target'],
  ['goals', 'Goals'],
  ['assists', 'Assists'],
  ['tackles', 'Tackles won'],
  ['fouls_committed', 'Fouls committed'],
  ['fouls_drawn', 'Fouls drawn'],
  ['cards', 'Cards'],
]

const shortDate = (d: string) => new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
const seasonFmt = (s: string) => (s.length === 4 ? `20${s.slice(0, 2)}-${s.slice(2)}` : s)

export default function LeaderboardPage() {
  const [leagues, setLeagues] = useState<Competition[]>([])
  const [competitionId, setCompetitionId] = useState<number | null>(null)
  const [board, setBoard] = useState<Leaderboard | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .competitions()
      .then((comps) => {
        if (cancelled) return
        const l = comps.filter((c) => c.type === 'club_league')
        setLeagues(l)
        setCompetitionId(l[0]?.id ?? null)
      })
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (competitionId === null) return
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .leaderboard(competitionId)
      .then((b) => !cancelled && setBoard(b))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [competitionId])

  const clubs = useMemo(
    () => new Map((board?.clubs ?? []).map((c) => [c.team_id, c])),
    [board],
  )

  return (
    <div>
      <div className="mb-1 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold text-ink">Players</h1>
        <select
          value={competitionId ?? ''}
          onChange={(e) => setCompetitionId(Number(e.target.value))}
          className="rounded border border-line bg-card px-2 py-1 text-sm text-ink"
        >
          {leagues.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </div>
      <p className="mb-3 text-sm text-muted">
        Top 10 per 90 minutes, each player over his club’s last {board?.window ?? 10} League games
        with player data. He needs half the window’s minutes (450 for 10) to be ranked.
        {board && ` Clubs in the ${seasonFmt(board.season)} ${board.competition}.`}
      </p>

      {error && <p className="mb-3 text-sm text-rose-700">{error}</p>}
      {!board ? (
        !error && <p className="text-muted">Loading…</p>
      ) : (
        <div className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
          <WindowNotes clubs={board.clubs} />
          <div className="grid gap-4 sm:grid-cols-2">
            {LISTS.map(([metric, label]) => (
              <List key={metric} label={label} rows={board.categories[metric]} clubs={clubs} cards={metric === 'cards'} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** Says where a club window is not simply "this season's last 10": it crosses
 * divisions, it is short, or a game is waiting for its player data. */
function WindowNotes({ clubs }: { clubs: LeaderClub[] }) {
  const crossing = clubs.filter((c) => c.competitions.length > 1)
  const short = clubs.filter((c) => c.games < 10)
  // two clubs share each awaiting game: name it once (no home/away is known here)
  const awaiting = new Map<number, string>()
  for (const c of clubs)
    for (const g of c.awaiting)
      if (!awaiting.has(g.fixture_id)) awaiting.set(g.fixture_id, `${c.team}–${g.opponent} (${shortDate(g.date)})`)
  if (!crossing.length && !short.length && !awaiting.size) return null
  return (
    <div className="mb-4 space-y-1 text-xs text-faint">
      {crossing.length > 0 && (
        <p>
          Window crosses divisions:{' '}
          {crossing.map((c) => `${c.team} (includes ${c.competitions.slice(1).join(', ')})`).join('; ')}.
        </p>
      )}
      {short.length > 0 && (
        <p>
          Fewer than 10 League games on record: {short.map((c) => `${c.team} (${c.games})`).join(', ')}.
        </p>
      )}
      {awaiting.size > 0 && (
        <p>
          Awaiting player data, not yet counted:{' '}
          {[...awaiting].map(([id, game], i) => (
            <span key={id}>
              {i > 0 && ', '}
              <EntityLink to={matchHref(id)}>{game}</EntityLink>
            </span>
          ))}
          .
        </p>
      )}
    </div>
  )
}

function List({
  label, rows, clubs, cards,
}: {
  label: string
  rows: LeaderRow[]
  clubs: Map<number, LeaderClub>
  cards: boolean
}) {
  const num = 'px-1.5 py-1 text-right tabular-nums'
  return (
    <div className="rounded-lg border border-line bg-card">
      <h2 className="border-b border-line px-3 py-2 text-sm font-semibold text-ink">{label}</h2>
      {rows.length === 0 ? (
        <p className="px-3 py-2 text-xs text-faint">No player has enough minutes yet.</p>
      ) : (
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="text-muted">
              <th className="w-6 py-1 pl-3 text-left font-normal">#</th>
              <th className="py-1 pr-2 text-left font-normal">Player</th>
              <th className={`${num} font-normal`} title="Per 90 minutes: the ranking figure">/90</th>
              <th className={`${num} font-normal`} title="Total in the window">Tot</th>
              <th className={`${num} font-normal`} title="Minutes the rate is over">Min</th>
              <th className={`${num} pr-3 font-normal`} title="Games he played of his club's window">
                {cards ? 'Carded' : 'Apps'}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const club = clubs.get(r.team_id)
              const window = `${club?.team ?? 'His club'}’s last ${club?.games ?? 10} League games: played ${r.apps}, ${r.minutes} min`
              return (
                <tr key={r.player_id} className="border-t border-line-soft hover:bg-sunken">
                  <td className="py-1 pl-3 text-muted">{i + 1}</td>
                  <td className="py-1 pr-2">
                    <EntityLink to={playerHref(r.player_id)} className="font-medium text-ink">
                      {r.player}
                    </EntityLink>
                    <div className="flex items-center gap-1 text-faint">
                      <KitShirt kit={kitOf(r.team_id)} className="h-3 w-3" />
                      <EntityLink to={teamHref(r.team_id)}>{club?.team ?? '—'}</EntityLink>
                    </div>
                  </td>
                  <td className={`${num} font-semibold text-ink`}>{r.per90.toFixed(2)}</td>
                  <td className={`${num} text-ink-2`}>{r.total}</td>
                  <td className={`${num} text-muted`}>{r.minutes}′</td>
                  <td className={`${num} pr-3 text-muted`} title={window}>
                    {cards ? `${r.carded_apps} of ${r.apps}` : `${r.apps}/${club?.games ?? 10}`}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
