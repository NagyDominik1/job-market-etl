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

const TIMEOUT_MESSAGE =
  'A keresés túllépte az időkorlátot, a szerver leállította, ezért nem lett eredmény. ' +
  'Ez nem számít bele az óránkénti keresésbe: pár perc múlva újrapróbálhatod, ' +
  'esetleg kevesebb várossal vagy szerepkörrel.'

// Errors from functions.invoke(), as a clear Hungarian message:
//  - FunctionsFetchError: no answer at all (network, or the connection was cut)
//  - our function's JSON error {error, next_allowed_at?} (429, 400, 502...)
//  - a non-2xx WITHOUT our JSON: Supabase stopped the function (time limit)
async function describeFunctionError(error) {
  if (error.name === 'FunctionsFetchError') {
    return 'Nem jött válasz a szervertől (hálózati hiba vagy időtúllépés). Próbáld újra pár perc múlva.'
  }
  const status = error.context?.status
  let body = null
  try {
    body = await error.context.json()
  } catch {
    // not our JSON: the function was stopped before it could answer
  }
  if (body?.next_allowed_at) return `Óránként egy keresés. Következő: ${formatTime(body.next_allowed_at)}`
  if (body?.error) {
    if (/time limit|timed out|timeout/i.test(body.error)) return TIMEOUT_MESSAGE
    return `A keresés nem sikerült: ${body.error}`
  }
  if (status === 504 || status === 546 || status === undefined) return TIMEOUT_MESSAGE
  return `A keresés nem sikerült (HTTP ${status}). Próbáld újra pár perc múlva.`
}

// Per-run counts (search_runs, migration 010): from the function's response
// right after a search, or from the run row. null when the run has none (older
// runs, or 010 not applied yet).
function countsOf(run, fromResponse) {
  if (fromResponse) return fromResponse
  if (run && typeof run.fetches_used === 'number' && run.candidates !== null && run.candidates !== undefined) return run
  return null
}

const DROP_LABELS = [
  ['dropped_unconfirmed', 'nem ellenőrzött (nem nyitotta meg)'],
  ['dropped_senior', 'senior / lead szint'],
  ['dropped_years', 'túl sok év tapasztalat'],
  ['dropped_dealbreaker', 'kizáró szó a címben'],
  ['dropped_bad_url', 'lista- vagy keresőoldal link'],
  ['dropped_other', 'hibás vagy duplikált'],
]

function countsLine(counts) {
  const drops = DROP_LABELS.filter(([key]) => counts[key] > 0).map(([key, label]) => `${counts[key]} ${label}`)
  return `Jelöltek: ${counts.candidates} · megnyitott hirdetés: ${counts.fetches_used}` +
    (drops.length ? ` · kiszűrve: ${drops.join(', ')}` : '')
}

// Why a successful job search ended with 0 results, in one sentence.
function emptyReason(counts) {
  if (!counts) return 'Nincs találat.'
  if (counts.candidates === 0 && counts.fetches_used === 0) {
    return 'Nincs találat: a keresések nem hoztak megnyitható hirdetést, így Claude egyet sem nyitott meg. ' +
      'Próbáld más városokkal, kevesebb vagy általánosabb szerepkörrel.'
  }
  if (counts.candidates === 0) {
    return `Nincs találat: Claude ${counts.fetches_used} hirdetést megnyitott, de egyik sem felelt meg a feltételeidnek.`
  }
  const [key, label] = DROP_LABELS.reduce((best, item) => (counts[item[0]] > counts[best[0]] ? item : best))
  return `Nincs találat: mind a ${counts.candidates} jelöltet kiszűrtük; a leggyakoribb ok: ${label} (${counts[key]}).`
}

// The last run's status line: success, still running, or why it failed.
function runOutcome(run, resultCount) {
  if (run.status === 'success') return `${resultCount} találat`
  if (run.status === 'running') return 'még fut (ha 5 percnél régebbi, a következő keresés lezárja)'
  if (/timeout|time limit/i.test(run.error ?? '')) return 'időtúllépés, nincs eredmény'
  return `hiba: ${run.error ?? run.status}`
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
  const [notice, setNotice] = useState(null) // e.g. "stopped early, the list may be incomplete"
  const [responseCounts, setResponseCounts] = useState(null) // counts from the last search's response

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
    setNotice(null)
    setResponseCounts(null)
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
      setError(await describeFunctionError(error))
      setResults([])
      // Show the failed run's row too (its stored reason), if it has one.
      const { data: runs } = await supabase.from('search_runs').select('*').order('started_at', { ascending: false }).limit(1)
      setRun(runs?.[0] ?? null)
    } else {
      setResults(data.results)
      setResponseCounts(data.counts ?? null)
      if (data.stopped_early) {
        setNotice('Időkorlát miatt a keresés korábban leállt: az eddig ellenőrzött találatok mentve, a lista hiányos lehet.')
      }
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
        <span className="hint">Óránként egy keresés; legfeljebb 8 webes keresés (~$0.08) és kb. 1,5 perc.</span>
      </form>

      {error && <p className="status error" role="alert">{error}</p>}
      {notice && <p className="status notice">{notice}</p>}

      {run && !loading && (
        <p className="status">
          Utolsó keresés: {formatTime(run.started_at)} · {run.mode === 'jobs' ? 'állások' : 'cégek'} ·{' '}
          {runOutcome(run, results.length)} ·{' '}
          {run.searches_used} webes keresés, {run.input_tokens + run.output_tokens} token · kb. $
          {estimateCost(run).toFixed(3)}
        </p>
      )}

      {run && !loading && run.mode === 'jobs' && countsOf(run, responseCounts) && (
        <p className="status">{countsLine(countsOf(run, responseCounts))}</p>
      )}
      {run && !loading && run.mode === 'jobs' && run.status === 'success' && results.length === 0 && (
        <p className="status notice">{emptyReason(countsOf(run, responseCounts))}</p>
      )}

      <div className="cards">
        {results.map((result) => (
          <ResultCard key={result.id} result={result} />
        ))}
      </div>
    </div>
  )
}
