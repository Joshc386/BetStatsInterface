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

export interface H2HRow {
  date: string
  season: string
  is_home: boolean
  gf: number | null
  ga: number | null
  result: string | null
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
  h2h: (teamId: number, opponentId: number) =>
    getJSON<H2HRow[]>(`/teams/${teamId}/h2h/${opponentId}`),
}
