import { Link } from 'react-router-dom'

/** One look for every team/player link, so the app reads as one graph rather
 * than a set of islands. Falls back to plain text when the id is missing —
 * an opponent with no id (or an uncovered club) must still render. */
export function EntityLink({
  to,
  children,
  className = '',
}: {
  to: string | null
  children: React.ReactNode
  className?: string
}) {
  if (!to) return <span className={className}>{children}</span>
  return (
    <Link
      to={to}
      className={`rounded-sm underline-offset-2 hover:text-sky-300 hover:underline focus-visible:outline-1 focus-visible:outline-sky-500 ${className}`}
    >
      {children}
    </Link>
  )
}

export const teamHref = (id: number | null | undefined) =>
  id === null || id === undefined ? null : `/team/${id}`

export const playerHref = (id: number | null | undefined) =>
  id === null || id === undefined ? null : `/player/${id}`
