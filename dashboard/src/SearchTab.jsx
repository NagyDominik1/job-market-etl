import { useEffect, useState } from 'react'
import { supabase } from './supabase.js'
import './search.css'

// The "Keresés" tab (Phase 9.3): starts the Edge Function "search" and shows
// its results. The browser never talks to Claude: it only calls our function
// (with my login token) and reads search_runs / search_results (read-only).

const SENIORITIES = ['intern', 'junior', 'mid', 'senior', 'lead']

// Rough cost (USD): Haiku 5.5 $0.10 / $0.50 per million tokens (prompts up
// to 100K tokens), web search $10 per 1000 searches. An estimate only; the
// real bill is in the Anthropic Console.
function estimateCost(run) {
  return run.searches_used * 0.01 + (run.input_tokens * 0.1 + run.output_tokens * 0.5) / 1_000_000
}

// "HU, RS" -> ["HU", "RS"]
function splitList(text) {
  return text.split(',').map((item) => item.trim()).filter(Boolean)
}

// Links come from web pages: render them only if they are http(s). The
// database and the Edge Function check this too; this is the third check.
function safeHref(url) {
  return typeof url === 'string' && /^https?:\/\//i.test(url) ? url : null
}

function contactHref(value) {
  if (!value) return null
  if (/^[^\s@]+@[^\s@]+\.[a-z]{2,}$/i.test(value)) return `mailto:${value}`
  return safeHref(value)
}

function scoreClass(score) {
  if (score >= 70) return 'score high'
  if (score >= 40) return 'score mid'
  return 'score low'
}

function reasonClass(reason) {
  if (reason.startsWith('+')) return 'plus'
  if (reason.startsWith('-')) return 'minus'
  return 'neutral'
}

function formatTime(value) {
  return new Date(value).toLocaleString('hu-HU', { dateStyle: 'short', timeStyle: 'short' })
}

// The Edge Function answers errors with JSON {error, next_allowed_at?}.
async function readFunctionError(error) {
  try {
    const body = await error.context.json()
    if (body.next_allowed_at) return `Óránként egy keresés. Következő: ${formatTime(body.next_allowed_at)}`
    return body.error || error.message
  } catch {
    return error.message
  }
}

function ResultCard({ result }) {
  const href = safeHref(result.url)
  const contact = contactHref(result.contact)
  const heading = result.title || result.company_name
  return (
    <article className="card">
      <header className="card-head">
        <span className={scoreClass(result.score)} title="AI score">{result.score}</span>
        <div>
          <h2>
            {href ? (
              <a href={href} target="_blank" rel="noopener noreferrer">{heading}</a>
            ) : (
              heading
            )}
            {result.already_applied && <span className="badge applied">már jelentkeztél</span>}
          </h2>
          <p className="meta">
            {result.title && result.company_name}
            {result.city && `${result.title ? ' · ' : ''}${result.city}`}
            {result.remote && ' · remote'}
          </p>
        </div>
      </header>
      {result.reasons?.length > 0 && (
        <ul className="reasons">
          {result.reasons.map((reason) => (
            <li key={reason} className={reasonClass(reason)}>{reason}</li>
          ))}
        </ul>
      )}
      {(result.deadline || contact) && (
        <footer className="card-foot">
          {result.deadline && <span>Határidő: {new Date(result.deadline).toLocaleDateString('hu-HU')}</span>}
          {contact && (
            <span>
              Kapcsolat:{' '}
              <a href={contact} target="_blank" rel="noopener noreferrer">{result.contact}</a>
            </span>
          )}
        </footer>
      )}
    </article>
  )
}

export default function SearchTab({ session }) {
  const [mode, setMode] = useState('jobs')
  const [countries, setCountries] = useState('HU, RS')
  const [cities, setCities] = useState('')
  const [remote, setRemote] = useState(true)
  const [roles, setRoles] = useState('')
  const [seniority, setSeniority] = useState(['junior'])
  const [languages, setLanguages] = useState('')

  const [run, setRun] = useState(null)        // the last search_runs row
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const [error, setError] = useState(null)

  // On open: fill the form from search_profile, and show the last search.
  useEffect(() => {
    async function load() {
      const [{ data: profile }, { data: runs }] = await Promise.all([
        supabase.from('search_profile').select('*').maybeSingle(),
        supabase.from('search_runs').select('*').order('started_at', { ascending: false }).limit(1),
      ])
      if (profile) {
        setCities(profile.cities.filter((city) => city !== 'remote').join(', '))
        setRemote(profile.cities.includes('remote'))
        setRoles(profile.preferred_roles.join(', '))
        if (profile.seniority.length) setSeniority(profile.seniority)
        setLanguages(Object.keys(profile.languages).join(', '))
      }
      const last = runs?.[0]
      if (last) {
        setRun(last)
        setMode(last.mode)
        const { data } = await supabase
          .from('search_results')
          .select('*')
          .eq('run_id', last.id)
          .order('score', { ascending: false })
        setResults(data ?? [])
      }
    }
    load()
  }, [])

  // A visible clock while waiting: a search takes 30-120 seconds.
  useEffect(() => {
    if (!loading) return
    setSeconds(0)
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => clearInterval(timer)
  }, [loading])

  function toggleSeniority(level) {
    setSeniority((current) => (current.includes(level) ? current.filter((l) => l !== level) : [...current, level]))
  }

  async function runSearch(event) {
    event.preventDefault()
    setLoading(true)
    setError(null)
    const params = {
      countries: splitList(countries),
      cities: splitList(cities),
      remote,
      roles: splitList(roles),
      seniority,
      languages: splitList(languages),
    }
    // invoke() sends my login token automatically (Authorization header).
    const { data, error } = await supabase.functions.invoke('search', { body: { mode, params } })
    if (error) {
      setError(await readFunctionError(error))
    } else {
      setResults(data.results)
      const { data: runs } = await supabase.from('search_runs').select('*').eq('id', data.run_id)
      setRun(runs?.[0] ?? null)
    }
    setLoading(false)
  }

  return (
    <div className="page">
      <header className="topbar">
        <h1>Keresés</h1>
        <span className="who">{session.user.email}</span>
        <button className="secondary" onClick={() => supabase.auth.signOut()}>
          Log out
        </button>
      </header>

      <form className="search-form" onSubmit={runSearch}>
        <div className="mode-switch" role="radiogroup" aria-label="Mit keresel?">
          <button type="button" className={mode === 'jobs' ? '' : 'secondary'} onClick={() => setMode('jobs')}>
            Állások
          </button>
          <button type="button" className={mode === 'companies' ? '' : 'secondary'} onClick={() => setMode('companies')}>
            Cégek (nyílt jelentkezés)
          </button>
        </div>

        <div className="filters">
          <label>
            Országok
            <input value={countries} onChange={(e) => setCountries(e.target.value)} placeholder="HU, RS" />
          </label>
          <label>
            Városok (sorrend = prioritás)
            <input value={cities} onChange={(e) => setCities(e.target.value)} placeholder="Szeged, Szabadka" />
          </label>
          <label>
            Szerepkörök
            <input value={roles} onChange={(e) => setRoles(e.target.value)} placeholder="php developer, backend" />
          </label>
          <label>
            Nyelvek
            <input value={languages} onChange={(e) => setLanguages(e.target.value)} placeholder="hungarian, english" />
          </label>
        </div>

        <div className="checks">
          <label>
            <input type="checkbox" checked={remote} onChange={(e) => setRemote(e.target.checked)} /> remote is
          </label>
          {SENIORITIES.map((level) => (
            <label key={level}>
              <input type="checkbox" checked={seniority.includes(level)} onChange={() => toggleSeniority(level)} /> {level}
            </label>
          ))}
        </div>

        <button type="submit" disabled={loading}>
          {loading ? `Keresés… ${seconds} s` : 'Keresés indítása'}
        </button>
        <span className="hint">Óránként egy keresés; legfeljebb 15 webes keresés (~$0.15) és kb. 2 perc.</span>
      </form>

      {error && <p className="status error">{error}</p>}

      {run && !loading && (
        <p className="status">
          Utolsó keresés: {formatTime(run.started_at)} · {run.mode === 'jobs' ? 'állások' : 'cégek'} ·{' '}
          {run.status === 'success' ? `${results.length} találat` : `hiba: ${run.error ?? run.status}`} ·{' '}
          {run.searches_used} webes keresés, {run.input_tokens + run.output_tokens} token · kb. $
          {estimateCost(run).toFixed(3)}
        </p>
      )}

      <div className="cards">
        {results.map((result) => (
          <ResultCard key={result.id} result={result} />
        ))}
      </div>
    </div>
  )
}
