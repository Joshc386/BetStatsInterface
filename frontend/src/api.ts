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
  opponent_id?: number | null
  opponent: string | null
  is_home: boolean
  value: number | null
  // W/D/L for that match — the player's club's result. Null when no team row
  // exists for the appearance, so the chip must tolerate its absence.
  result?: string | null
  minutes: number | null
  // players only — club + competition for that appearance (Spell grouping)
  team_id?: number | null
  team?: string | null
  competition_id?: number | null
  competition?: string | null
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
  h2h: FixtureRow[] // both sides of each meeting — league + domestic cups
}

// One scheduled fixture from the upcoming feed (ESPN, ADR 0009) —
// display-only; links into the Fixture view by team pair.
export interface UpcomingFixture {
  fixture_id: number
  date: string
  competition: string
  home_id: number
  home_name: string
  away_id: number
  away_name: string
}

// One club's line in a computed league table (ADR 0010). `adjustment` is the
// season's administrative points change (negative = deduction), already
// included in `points`.
export interface TableRow {
  position: number
  team_id: number
  team_name: string
  played: number
  won: number
  drawn: number
  lost: number
  gf: number
  ga: number
  gd: number
  points: number
  adjustment: number
  adjustment_note: string | null
}

// Squad-form panel — the club's Recent squad + each member's raw appearance rows
// at the club, which the client windows + aggregates (mirrors backend SquadForm;
// see docs/adr/0006). Membership = club of the player's most-recent appearance.
export interface SquadMember {
  player_id: number
  player: string
  last_seen: string // his most-recent appearance; old date => likely a "ghost"
}

export interface SquadAppearanceRow {
  player_id: number
  player: string
  date: string
  season: string
  competition_id: number
  competition: string
  competition_type: string
  opponent_id: number
  opponent: string
  is_home: boolean
  minutes: number | null
  goals: number | null
  assists: number | null
  shots: number | null
  sot: number | null
  tackles: number | null
  fouls_drawn: number | null
  fouls_committed: number | null
  yellows: number | null
  reds: number | null
  second_yellows: number | null
  carded: boolean | null
}

export interface SquadForm {
  team_id: number
  team_name: string
  members: SquadMember[]
  rows: SquadAppearanceRow[]
}

async function getJSON<T>(path: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`)
  } catch {
    // fetch rejects (not an HTTP error) when the API is unreachable.
    throw new Error(`Cannot reach the API at ${API_BASE} — is the backend running?`)
  }
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
  seasons: () => getJSON<{ team: string[]; player: string[] }>('/seasons'),
  summary: (
    entity: Entity,
    id: number,
    params: Record<string, string>,
    seasons?: string[],
  ) => {
    const sp = new URLSearchParams(params)
    seasons?.forEach((s) => sp.append('seasons', s)) // season mode: repeated param
    return getJSON<Summary>(
      `/${entity === 'team' ? 'teams' : 'players'}/${id}/summary?${sp}`,
    )
  },
  fixturesCompare: (home: number, away: number, n: number, scopes: string[]) => {
    const sp = new URLSearchParams({ home: String(home), away: String(away), n: String(n) })
    scopes.forEach((s) => sp.append('scope', s)) // form-window scopes; H2H is always all scopes
    return getJSON<FixtureComparison>(`/fixtures/compare?${sp}`)
  },
  fixtureDetail: (fixtureId: number) =>
    getJSON<FixtureRow[]>(`/fixtures/${fixtureId}`),
  fixturesUpcoming: (days = 14) =>
    getJSON<UpcomingFixture[]>(`/fixtures/upcoming?days=${days}`),
  squadForm: (teamId: number, cap = 30) =>
    getJSON<SquadForm>(`/teams/${teamId}/squad-form?cap=${cap}`),
  table: (competitionId: number, season?: string) => {
    const sp = new URLSearchParams({ competition_id: String(competitionId) })
    if (season) sp.set('season', season)
    return getJSON<TableRow[]>(`/table?${sp}`)
  },
}
