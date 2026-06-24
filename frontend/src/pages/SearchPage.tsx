import SearchBar from '../components/SearchBar'

export default function SearchPage() {
  return (
    <div className="mx-auto max-w-xl pt-16 text-center">
      <h1 className="mb-2 text-2xl font-semibold text-slate-100">
        Find a team or player
      </h1>
      <p className="mb-6 text-sm text-slate-500">
        Rolling-window form over the last N games, within a competition scope.
      </p>
      <SearchBar />
      <p className="mt-8 text-xs text-slate-600">
        Team data: top 4 English tiers, 6 seasons. Player data: Premier League,
        3 seasons.
      </p>
    </div>
  )
}
