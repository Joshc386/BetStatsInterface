import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Competition, type TableRow } from '../api'

// Computed league table (ADR 0010) — standings derived from our own team_match
// rows; points deductions applied and footnoted, never fetched from a provider.
export default function TablePage() {
  const [competitions, setCompetitions] = useState<Competition[]>([])
  const [seasons, setSeasons] = useState<string[]>([])
  const [competitionId, setCompetitionId] = useState<number | null>(null)
  const [season, setSeason] = useState<string>('')
  const [rows, setRows] = useState<TableRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([api.competitions(), api.seasons()])
      .then(([comps, s]) => {
        if (cancelled) return
        const leagues = comps.filter((c) => c.type === 'club_league')
        setCompetitions(leagues)
        setSeasons(s.team)
        setCompetitionId(leagues[0]?.id ?? null)
        setSeason(s.team[0] ?? '')
      })
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (competitionId === null || !season) return
    let cancelled = false
    setError(null)
    api
      .table(competitionId, season)
      .then((d) => !cancelled && setRows(d))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
    return () => {
      cancelled = true
    }
  }, [competitionId, season])

  const deductions = useMemo(
    () => (rows ?? []).filter((r) => r.adjustment !== 0),
    [rows],
  )

  if (error) return <p className="text-sm text-rose-700">{error}</p>

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold text-ink">League table</h1>
        <select
          value={competitionId ?? ''}
          onChange={(e) => setCompetitionId(Number(e.target.value))}
          className="rounded border border-line bg-card px-2 py-1 text-sm text-ink"
        >
          {competitions.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <select
          value={season}
          onChange={(e) => setSeason(e.target.value)}
          className="rounded border border-line bg-card px-2 py-1 text-sm text-ink"
        >
          {seasons.map((s) => (
            <option key={s} value={s}>
              {s.slice(0, 2)}/{s.slice(2)}
            </option>
          ))}
        </select>
      </div>

      {rows === null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted">No results for this season.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="px-3 py-2 text-right">#</th>
                <th className="px-3 py-2">Team</th>
                <th className="px-3 py-2 text-right">P</th>
                <th className="px-3 py-2 text-right">W</th>
                <th className="px-3 py-2 text-right">D</th>
                <th className="px-3 py-2 text-right">L</th>
                <th className="px-3 py-2 text-right">GF</th>
                <th className="px-3 py-2 text-right">GA</th>
                <th className="px-3 py-2 text-right">GD</th>
                <th className="px-3 py-2 text-right">Pts</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line-soft">
              {rows.map((r) => (
                <tr key={r.team_id} className="hover:bg-sunken">
                  <td className="px-3 py-1.5 text-right text-muted">
                    {r.position}
                  </td>
                  <td className="px-3 py-1.5">
                    <Link
                      to={`/team/${r.team_id}`}
                      className="text-ink hover:underline"
                    >
                      {r.team_name}
                    </Link>
                    {r.adjustment !== 0 && (
                      <span
                        title={r.adjustment_note ?? undefined}
                        className="ml-1 cursor-help text-rose-700"
                      >
                        *
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-right text-muted">{r.played}</td>
                  <td className="px-3 py-1.5 text-right text-muted">{r.won}</td>
                  <td className="px-3 py-1.5 text-right text-muted">{r.drawn}</td>
                  <td className="px-3 py-1.5 text-right text-muted">{r.lost}</td>
                  <td className="px-3 py-1.5 text-right text-muted">{r.gf}</td>
                  <td className="px-3 py-1.5 text-right text-muted">{r.ga}</td>
                  <td className="px-3 py-1.5 text-right text-muted">
                    {r.gd > 0 ? `+${r.gd}` : r.gd}
                  </td>
                  <td className="px-3 py-1.5 text-right font-medium text-ink">
                    {r.points}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {deductions.length > 0 && (
        <div className="mt-3 space-y-1 text-xs text-muted">
          {deductions.map((r) => (
            <p key={r.team_id}>
              <span className="text-rose-700">*</span> {r.team_name}{' '}
              {r.adjustment}: {r.adjustment_note}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
