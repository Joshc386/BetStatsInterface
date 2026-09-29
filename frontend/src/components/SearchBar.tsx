import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type SearchHit } from '../api'

export default function SearchBar({ compact = false }: { compact?: boolean }) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()
  const boxRef = useRef<HTMLDivElement>(null)

  // Debounced search; backend requires q length >= 2.
  useEffect(() => {
    const term = q.trim()
    if (term.length < 2) {
      setHits([])
      setError(null)
      return
    }
    let cancelled = false
    const t = setTimeout(() => {
      api
        .search(term)
        .then((r) => {
          if (!cancelled) {
            setHits(r)
            setOpen(true)
            setError(null)
          }
        })
        .catch((e) => {
          if (!cancelled) {
            setError(String(e.message ?? e))
            setHits([])
            setOpen(true) // surface the error instead of failing silently
          }
        })
    }, 200)
    return () => {
      cancelled = true
      clearTimeout(t)
    }
  }, [q])

  // Close dropdown on outside click.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node))
        setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const go = (h: SearchHit) => {
    setOpen(false)
    setQ('')
    navigate(`/${h.entity}/${h.id}`)
  }

  return (
    <div ref={boxRef} className="relative">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => hits.length && setOpen(true)}
        placeholder="Search team or player…"
        className={`w-full rounded-md border border-line bg-card px-3 text-ink placeholder:text-muted outline-none focus:border-accent-ink ${
          compact ? 'py-1.5 text-sm' : 'py-2.5 text-base'
        }`}
      />
      {open && (hits.length > 0 || error) && (
        <ul className="absolute z-20 mt-1 w-full overflow-hidden rounded-md border border-line bg-card shadow-xl">
          {error && <li className="px-3 py-2 text-sm text-rose-700">{error}</li>}
          {hits.map((h) => (
            <li key={`${h.entity}-${h.id}`}>
              <button
                onClick={() => go(h)}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-sunken"
              >
                <span className="text-ink">{h.name}</span>
                <span
                  className={`ml-3 rounded px-1.5 py-0.5 text-xs ${
                    h.entity === 'team'
                      ? 'bg-sky-100 text-sky-800'
                      : 'bg-emerald-100 text-emerald-800'
                  }`}
                >
                  {h.entity}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
