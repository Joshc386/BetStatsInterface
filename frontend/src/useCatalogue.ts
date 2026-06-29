import { useEffect, useState } from 'react'
import { api, type Competition, type MetricsCatalogue } from './api'

interface Catalogue {
  competitions: Competition[]
  metrics: MetricsCatalogue | null
  seasons: { team: string[]; player: string[] } | null
  error: string | null
}

// Loads the (small, static) reference data once: competition list + metric names
// + the seasons present per entity (the season-window control's source list).
export function useCatalogue(): Catalogue {
  const [competitions, setCompetitions] = useState<Competition[]>([])
  const [metrics, setMetrics] = useState<MetricsCatalogue | null>(null)
  const [seasons, setSeasons] = useState<{ team: string[]; player: string[] } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.competitions(), api.metrics(), api.seasons()])
      .then(([c, m, s]) => {
        setCompetitions(c)
        setMetrics(m)
        setSeasons(s)
      })
      .catch((e) => setError(String(e.message ?? e)))
  }, [])

  return { competitions, metrics, seasons, error }
}
