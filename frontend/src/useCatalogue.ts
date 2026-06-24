import { useEffect, useState } from 'react'
import { api, type Competition, type MetricsCatalogue } from './api'

interface Catalogue {
  competitions: Competition[]
  metrics: MetricsCatalogue | null
  error: string | null
}

// Loads the (small, static) reference data once: competition list + metric names.
export function useCatalogue(): Catalogue {
  const [competitions, setCompetitions] = useState<Competition[]>([])
  const [metrics, setMetrics] = useState<MetricsCatalogue | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.competitions(), api.metrics()])
      .then(([c, m]) => {
        setCompetitions(c)
        setMetrics(m)
      })
      .catch((e) => setError(String(e.message ?? e)))
  }, [])

  return { competitions, metrics, error }
}
