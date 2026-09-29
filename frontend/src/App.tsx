import { Link, Outlet } from 'react-router-dom'
import SearchBar from './components/SearchBar'

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-line bg-card/85 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center gap-4 px-4 py-3">
          <Link to="/" className="whitespace-nowrap font-semibold text-ink">
            BetStats <span className="font-normal text-muted">research</span>
          </Link>
          <div className="max-w-md flex-1">
            <SearchBar compact />
          </div>
          <nav className="flex shrink-0 items-center gap-4 text-sm">
            <Link to="/" className="whitespace-nowrap text-muted hover:text-ink">
              Fixtures
            </Link>
            <Link to="/table" className="whitespace-nowrap text-muted hover:text-ink">
              League table
            </Link>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <Outlet />
      </main>
      <footer className="mx-auto max-w-5xl px-4 py-6 text-xs text-faint">
        Research tool · reads only the local DB · covered competitions only.
      </footer>
    </div>
  )
}
