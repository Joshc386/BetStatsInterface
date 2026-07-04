import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import SearchBar from '../components/SearchBar'
import { api, type UpcomingFixture } from '../api'

const SHOW = 20 // nearest games only — the horizon self-adjusts across the season

export default function SearchPage() {
  return (
    <div className="mx-auto max-w-xl pt-16">
      <div className="text-center">
        <h1 className="mb-2 text-2xl font-semibold text-slate-100">
          Find a team or player
        </h1>
        <p className="mb-6 text-sm text-slate-500">
          Rolling-window form over the last N games, within a competition scope.
        </p>
        <SearchBar />
      </div>
      <UpcomingSlate />
      <p className="mt-8 text-center text-xs text-slate-600">
        Team data: top 4 English tiers, 6 seasons. Player data: Premier League
        &amp; Championship + domestic cups, 6 seasons.
      </p>
    </div>
  )
}

function UpcomingSlate() {
  const [fixtures, setFixtures] = useState<UpcomingFixture[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .fixturesUpcoming(60)
      .then((d) => !cancelled && setFixtures(d.slice(0, SHOW)))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
    return () => {
      cancelled = true
    }
  }, [])

  if (error) return null // the slate is a bonus — search must never break with it
  if (!fixtures || fixtures.length === 0) return null

  // group by calendar day, feed order is already date-ascending
  const days = new Map<string, UpcomingFixture[]>()
  for (const f of fixtures) {
    const key = new Date(f.date).toLocaleDateString('en-GB', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
    })
    const list = days.get(key) ?? []
    list.push(f)
    days.set(key, list)
  }

  return (
    <div className="mt-10">
      <h2 className="mb-3 text-sm font-medium text-slate-400">
        Upcoming fixtures
      </h2>
      <div className="space-y-4">
        {[...days.entries()].map(([day, games]) => (
          <div key={day}>
            <h3 className="mb-1 text-xs text-slate-500">{day}</h3>
            <div className="divide-y divide-slate-900 rounded-lg border border-slate-800 bg-slate-900/40">
              {games.map((f) => (
                <Link
                  key={f.fixture_id}
                  to={`/fixture/${f.home_id}/vs/${f.away_id}`}
                  className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-slate-900"
                >
                  <span className="w-12 shrink-0 text-xs text-slate-500">
                    {new Date(f.date).toLocaleTimeString('en-GB', {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>
                  <span className="flex-1 truncate text-right text-slate-200">
                    {f.home_name}
                  </span>
                  <span className="text-xs text-slate-600">vs</span>
                  <span className="flex-1 truncate text-slate-200">
                    {f.away_name}
                  </span>
                  <span className="w-24 shrink-0 truncate text-right text-xs text-slate-600">
                    {f.competition}
                  </span>
                </Link>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
