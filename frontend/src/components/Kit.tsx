import { useId, type ReactNode } from 'react'
import { themeStyle, type Kit, type Theme } from '../lib/teamTheme'

const SHIRT = 'M13 5 6 9 3 17 9 19 10 15V36H30V15l1 4 6-2-3-8-7-4q-7 5-14 0Z'

const PATTERNS = {
  stripes: [13, 21, 29].map((x) => <rect key={x} x={x - 2} y="0" width="4" height="40" />),
  hoops: [13, 21, 29].map((y) => <rect key={y} x="0" y={y - 2} width="40" height="4" />),
  halves: <rect x="20" y="0" width="20" height="40" />,
}

/** A small home shirt in a club's colours — how a team is marked in lists and
 * on its hub. Stripes/hoops/halves are drawn in the trim colour. */
export function KitShirt({ kit, className = 'h-4 w-4' }: { kit: Kit; className?: string }) {
  const clip = `kit${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  return (
    <svg viewBox="0 0 40 40" className={`shrink-0 ${className}`} aria-hidden="true">
      <defs>
        <clipPath id={clip}>
          <path d={SHIRT} />
        </clipPath>
      </defs>
      <path d={SHIRT} fill={kit.shirt} />
      {kit.pattern && (
        <g clipPath={`url(#${clip})`} fill={kit.trim}>
          {PATTERNS[kit.pattern]}
        </g>
      )}
      <path d={SHIRT} fill="none" stroke={kit.trim} strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M13 5q7 5 14 0" fill="none" stroke={kit.trim} strokeWidth="2.2" />
    </svg>
  )
}

/** The app's mark: a ball. */
export function BallMark({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 20 20" className={className} aria-hidden="true">
      <circle cx="10" cy="10" r="9" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M10 6.4l3.42 2.49-1.3 4.02H7.88l-1.3-4.02z" fill="currentColor" />
      <path
        d="M10 6.4V1M13.42 8.89l5.14-1.67M12.12 12.91l3.17 4.37M7.88 12.91l-3.17 4.37M6.58 8.89 1.44 7.22"
        stroke="currentColor"
        strokeWidth="1.3"
      />
    </svg>
  )
}

/** Chalk markings — halfway line, centre circle, a penalty box — faint behind a
 * hub header. Drawn in currentColor so they sit on any club's band. */
export function PitchLines({ className = '' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 200 100"
      preserveAspectRatio="xMaxYMid meet"
      className={`pointer-events-none ${className}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      aria-hidden="true"
    >
      <line x1="60" y1="0" x2="60" y2="100" />
      <circle cx="60" cy="50" r="24" />
      <circle cx="60" cy="50" r="1.6" fill="currentColor" />
      <rect x="152" y="16" width="48" height="68" />
      <rect x="182" y="36" width="18" height="28" />
      <path d="M152 40A15 15 0 0 0 152 60" />
    </svg>
  )
}

/** A hub's header band in a club's colours: shirt, name, one line beneath. It
 * sets the theme itself, so a Fixture view can put two side by side — the away
 * half `mirror`ed, so the pair faces each other across the halfway line. */
export function Hero({
  theme, title, subtitle, mirror = false, className = '',
}: {
  theme: Theme
  title: ReactNode
  subtitle?: ReactNode
  mirror?: boolean
  className?: string
}) {
  return (
    <div
      style={themeStyle(theme)}
      className={`relative overflow-hidden bg-accent text-on-accent ${className}`}
    >
      <PitchLines
        className={`absolute inset-0 h-full w-full opacity-20 ${mirror ? '-scale-x-100' : ''}`}
      />
      <div
        className={`relative flex items-center gap-4 px-5 py-4 ${mirror ? 'flex-row-reverse text-right' : ''}`}
      >
        <KitShirt kit={theme.kit} className="h-12 w-12" />
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-semibold">{title}</h1>
          {subtitle && <div className="text-sm">{subtitle}</div>}
        </div>
      </div>
    </div>
  )
}
