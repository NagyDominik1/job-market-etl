import { useEffect, useState } from 'react'
import { supabase } from './supabase.js'
import JobCard from './JobCard.jsx'
import { useAppliedJobIds } from './applications.js'

const PAGE_SIZE = 200 // the best 200 matches; Supabase returns at most 1000 rows per request

const PERIODS = {
  all: null,
  '7d': 7 * 24 * 60 * 60 * 1000,
  '24h': 24 * 60 * 60 * 1000,
}

// Remove characters that have a meaning in ILIKE patterns (% _) or in
// PostgREST's filter syntax (, ( ) " \ *), so typed text is matched literally.
export function cleanSearch(text) {
  return text.replace(/[%_,()"\\*]/g, ' ').trim()
}

// Wait until the user stops typing before querying the database.
export function useDebounced(value, delayMs = 300) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

export default function JobFeed({ session }) {
  const [minScore, setMinScore] = useState(30)
  const [city, setCity] = useState('')
  const [period, setPeriod] = useState('all')
  const [search, setSearch] = useState('')

  const [jobs, setJobs] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const debouncedMinScore = useDebounced(minScore)
  const debouncedCity = useDebounced(city)
  const debouncedSearch = useDebounced(search)
  const appliedJobIds = useAppliedJobIds(jobs.map((job) => job.id))

  useEffect(() => {
    let ignore = false // a newer query started: drop this one's result

    async function load() {
      setLoading(true)
      setError(null)
      // All filtering happens in the database, so the browser only gets the
      // rows it shows. count: 'exact' also returns how many rows match.
      let query = supabase
        .from('job_feed')
        .select('*', { count: 'exact' })
        .order('match_score', { ascending: false, nullsFirst: false })
        .order('first_seen_at', { ascending: false })
        .limit(PAGE_SIZE)

      if (debouncedMinScore > 0) query = query.gte('match_score', debouncedMinScore)
      const cityText = cleanSearch(debouncedCity)
      if (cityText) query = query.ilike('location', `%${cityText}%`)
      if (PERIODS[period]) {
        query = query.gte('first_seen_at', new Date(Date.now() - PERIODS[period]).toISOString())
      }
      const text = cleanSearch(debouncedSearch)
      if (text) query = query.or(`title.ilike.*${text}*,company_name.ilike.*${text}*`) // * = any text

      const { data, count, error } = await query
      if (ignore) return
      if (error) {
        setError(error.message)
        setJobs([])
      } else {
        setJobs(data)
        setTotal(count ?? data.length)
      }
      setLoading(false)
    }

    load()
    return () => {
      ignore = true
    }
  }, [debouncedMinScore, debouncedCity, period, debouncedSearch])

  return (
    <div className="page">
      <header className="topbar">
        <h1>Job feed</h1>
        <span className="who">{session.user.email}</span>
        <button className="secondary" onClick={() => supabase.auth.signOut()}>
          Log out
        </button>
      </header>

      <section className="filters" aria-label="Filters">
        <label>
          Min. score: <strong>{minScore}</strong>
          <input
            type="range"
            min="0"
            max="100"
            step="5"
            value={minScore}
            onChange={(event) => setMinScore(Number(event.target.value))}
          />
        </label>
        <label>
          City
          <input
            type="search"
            placeholder="e.g. Szeged, Remote"
            value={city}
            onChange={(event) => setCity(event.target.value)}
          />
        </label>
        <label>
          New
          <select value={period} onChange={(event) => setPeriod(event.target.value)}>
            <option value="all">All</option>
            <option value="7d">Last 7 days</option>
            <option value="24h">Last 24 hours</option>
          </select>
        </label>
        <label>
          Search
          <input
            type="search"
            placeholder="Title or company"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </section>

      {error && <p className="status error">Could not load the jobs: {error}</p>}
      {!error && (
        <p className="status">
          {loading ? 'Loading…' : `${total} job${total === 1 ? '' : 's'}`}
          {!loading && total > jobs.length && ` (showing the best ${jobs.length})`}
        </p>
      )}

      <div className="cards">
        {jobs.map((job) => (
          <JobCard key={job.id} job={job} applied={appliedJobIds.has(job.id)} />
        ))}
      </div>
    </div>
  )
}
