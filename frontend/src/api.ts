// Typed client over the read-only FastAPI. Mirrors backend/app/schemas.py.
// The app NEVER touches an external source — it reads only this local API.

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export type Entity = 'team' | 'player'

export interface SearchHit {
  entity: Entity
  id: number
  name: string
}

export interface Competition {
  id: number
  name: string
  type: string
  tier: number | null
}

export interface MetricsCatalogue {
  team: string[]
  player: string[]
  scopes: string[]
}

export interface HitRate {
  threshold: number
  direction: string
  hits: number
  n: number
  pct: number
}

export interface BreakdownRow {
  date: string
  opponent: string | null
  is_home: boolean
  value: number | null
  minutes: number | null
}

export interface Summary {
  entity: Entity
  entity_id: number
  entity_name: string | null
  metric: string
  scope: string
  window: string
  games: number
  total: number | null
  average: number | null
  per_appearance: number | null
  per_90: number | null
  minutes_total: number | null
  hit_rate: HitRate | null
  breakdown: BreakdownRow[]
}

// One team's perspective on a match — the raw row the Fixture view aggregates
// client-side (mirrors backend FixtureRow; see docs/adr/0005).
export interface FixtureRow {
  fixture_id: number
  team_id: number
  date: string
  season: string
  competition_id: number
  competition: string
  is_home: boolean
  opponent_id: number
  opponent: string
  gf: number | null
  ga: number | null
  shots: number | null
  sot: number | null
  shots_conceded: number | null
  sot_conceded: number | null
  corners: number | null
  fouls: number | null
  yellows: number | null
  reds: number | null
  total_goals: number | null
  btts: boolean | null
  clean_sheet: boolean | null
  result: string | null
}

export interface FixtureComparison {
  home_id: number
  home_name: string
  away_id: number
  away_name: string
  home: FixtureRow[]
  away: FixtureRow[]
  h2h: FixtureRow[] // both sides of each meeting
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${res.status}: ${detail}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  search: (q: string) =>
    getJSON<SearchHit[]>(`/search?q=${encodeURIComponent(q)}`),
  competitions: () => getJSON<Competition[]>('/competitions'),
  metrics: () => getJSON<MetricsCatalogue>('/metrics'),
  summary: (entity: Entity, id: number, params: Record<string, string>) =>
    getJSON<Summary>(
      `/${entity === 'team' ? 'teams' : 'players'}/${id}/summary?${new URLSearchParams(
        params,
      )}`,
    ),
  fixturesCompare: (home: number, away: number, n: number) =>
    getJSON<FixtureComparison>(
      `/fixtures/compare?home=${home}&away=${away}&n=${n}`,
    ),
  fixtureDetail: (fixtureId: number) =>
    getJSON<FixtureRow[]>(`/fixtures/${fixtureId}`),
}
