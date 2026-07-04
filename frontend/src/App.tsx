import { Link, Outlet } from 'react-router-dom'
import SearchBar from './components/SearchBar'

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/70 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center gap-4 px-4 py-3">
          <Link to="/" className="whitespace-nowrap font-semibold text-slate-100">
            BetStats <span className="font-normal text-slate-500">research</span>
          </Link>
          <div className="max-w-md flex-1">
            <SearchBar compact />
          </div>
          <Link
            to="/table"
            className="whitespace-nowrap text-sm text-slate-400 hover:text-slate-200"
          >
            League table
          </Link>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <Outlet />
      </main>
      <footer className="mx-auto max-w-5xl px-4 py-6 text-xs text-slate-600">
        Research tool · reads only the local DB · covered competitions only.
      </footer>
    </div>
  )
}
