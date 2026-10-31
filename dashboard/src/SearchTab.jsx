import { useEffect, useState } from 'react'
import { supabase } from './supabase.js'
import AddToApplications from './AddToApplications.jsx'
import LetterButton from './Letter.jsx'
import { safeHref, useAppliedCompanyKeys } from './applications.js'
import './search.css'

// The "Keresés" tab (Phase 9.3): starts the Edge Function "search" and shows
// its results. The browser never talks to Claude: it only calls our function
// (with my login token) and reads search_runs / search_results (read-only).
// A search runs in chunks (run 9): one call starts the run, then one call per
// city (plus remote), one after another, with progress per chunk. A failed
// chunk doesn't stop the others, and the finished chunks' results stay.
// A failed chunk can be retried once ("Újrapróbálás"), inside the same run.

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
function contactHref(value) {
  if (!value) return null
  if (/^[^\s@]+@[^\s@]+\.[a-z]{2,}$/i.test(value)) return `mailto:${value}`
  return safeHref(value)
}

// The reason the search function adds to staffing agencies and remote talent
// marketplaces (AGENCY_REASON in supabase/functions/search/validate.ts; the
// same text, so no extra column is needed). Shown as an "ügynökség" badge.
const AGENCY_REASON_PREFIX = '- staffing agency / talent marketplace'

function isAgencyResult(result) {
  return Boolean(result.reasons?.some((reason) => reason.startsWith(AGENCY_REASON_PREFIX)))
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
  ['dropped_other', 'hibás, duplikált vagy munkáltató nélküli (állásportál, kategória, „Confidential”)'],
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

const CHUNK_STATUS = {
  waiting: 'vár',
  running: 'fut…',
}

// "Szeged: kész, 4 találat" / "Subotica: hiba (…)"
function chunkLine(chunk) {
  const retried = chunk.retried ? ' (újrapróbálva)' : ''
  if (chunk.status === 'done') {
    return `kész, ${chunk.count} találat${chunk.stoppedEarly ? ' (időkorlát miatt hiányos)' : ''}${retried}`
  }
  if (chunk.status === 'error') return `hiba: ${chunk.message}${retried}`
  return CHUNK_STATUS[chunk.status]
}

// The run-level timeout text ("doesn't count, retry later") is wrong for one chunk.
async function describeChunkError(error) {
  const message = await describeFunctionError(error)
  return message === TIMEOUT_MESSAGE ? 'időtúllépés' : message
}

// The last run's status line: success, still running, or why it failed.
function runOutcome(run, resultCount) {
  if (run.status === 'success') return `${resultCount} találat`
  if (run.status === 'running') return 'még fut (ha 5 percnél régebbi, a következő keresés lezárja)'
  if (/timeout|time limit/i.test(run.error ?? '')) return 'időtúllépés, nincs eredmény'
  return `hiba: ${run.error ?? run.status}`
}

// applied: the stored snapshot (already_applied, set when the result was
// saved) OR a live match with my applications' company_keys, so a company I
// applied to after the search is highlighted too.
function ResultCard({ result, applied }) {
  const href = safeHref(result.url)
  const contact = contactHref(result.contact)
  const heading = result.title || result.company_name
  return (
    <article className={`card${applied ? ' applied-card' : ''}`}>
      <header className="card-head">
        <span className={scoreClass(result.score)} title="AI score">{result.score}</span>
        <div>
          <h2>
            {href ? (
              <a href={href} target="_blank" rel="noopener noreferrer">{heading}</a>
            ) : (
              heading
            )}
            {applied && <span className="badge applied">Már jelentkeztél</span>}
            {isAgencyResult(result) && (
              <span className="badge agency" title="Közvetítő vagy remote piactér, nem maga a munkáltató">ügynökség</span>
            )}
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
      <AddToApplications
        fields={{
          company_name: result.company_name,
          position: result.title, // companies: NULL = open application
          city: result.city,
          posting_url: href ?? safeHref(result.contact),
          deadline: result.deadline,
        }}
      />
      <div className="card-links">
        <LetterButton target={{ search_result_id: result.id }} />
      </div>
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
  const [chunks, setChunks] = useState([]) // [{label, status, count, message, retried}] of the current search
  const [chunkRunId, setChunkRunId] = useState(null) // the run the chunk list belongs to (for retries)
  const appliedKeys = useAppliedCompanyKeys()

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

  // A visible clock while waiting: about a minute per chunk.
  useEffect(() => {
    if (!loading) return
    setSeconds(0)
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => clearInterval(timer)
  }, [loading])

  function toggleSeniority(level) {
    setSeniority((current) => (current.includes(level) ? current.filter((l) => l !== level) : [...current, level]))
  }

  // The run's row and its stored results (also shows the already_applied badge).
  async function reloadRun(runId) {
    const [{ data: runs }, { data: rows }] = await Promise.all([
      supabase.from('search_runs').select('*').eq('id', runId),
      supabase.from('search_results').select('*').eq('run_id', runId).order('score', { ascending: false }),
    ])
    setRun(runs?.[0] ?? null)
    setResults(rows ?? [])
  }

  async function runSearch(event) {
    event.preventDefault()
    setLoading(true)
    setError(null)
    setNotice(null)
    setResponseCounts(null)
    setChunks([])
    const params = {
      countries: splitList(countries),
      cities: splitList(cities),
      remote,
      roles: splitList(roles),
      seniority,
      languages: splitList(languages),
    }
    // invoke() sends my login token automatically (Authorization header).
    // 1. Start the run: the rate limit is checked here, once per run.
    const { data: started, error } = await supabase.functions.invoke('search', { body: { action: 'start', mode, params } })
    if (error) {
      setError(await describeFunctionError(error))
      setResults([])
      // Show the failed run's row too (its stored reason), if it has one.
      const { data: runs } = await supabase.from('search_runs').select('*').order('started_at', { ascending: false }).limit(1)
      setRun(runs?.[0] ?? null)
      setLoading(false)
      return
    }

    // 2. The chunks, one after another. Results appear as each chunk finishes;
    // a failed chunk is shown and the next one still runs.
    setChunkRunId(started.run_id)
    let progress = started.chunks.map((label) => ({ label, status: 'waiting' }))
    const update = (index, fields) => {
      progress = progress.map((chunk, i) => (i === index ? { ...chunk, ...fields } : chunk))
      setChunks(progress)
    }
    setChunks(progress)
    setResults([])
    let found = []
    for (let index = 0; index < progress.length; index++) {
      update(index, { status: 'running' })
      const { data, error: chunkError } = await supabase.functions.invoke('search', {
        body: { action: 'chunk', run_id: started.run_id, chunk: index },
      })
      if (chunkError) {
        update(index, { status: 'error', message: await describeChunkError(chunkError) })
        continue
      }
      found = [...found, ...data.results].sort((a, b) => b.score - a.score)
      setResults(found)
      update(index, { status: 'done', count: data.results.length, stoppedEarly: data.stopped_early })
    }
    if (progress.some((chunk) => chunk.stoppedEarly)) {
      setNotice('Egyes városoknál időkorlát miatt korábban leállt a keresés: az eddig talált hirdetések mentve, a lista hiányos lehet.')
    }

    // 3. The run's totals (added up chunk by chunk in search_runs) and the stored results.
    await reloadRun(started.run_id)
    setLoading(false)
  }

  // "Újrapróbálás": one failed chunk once more, in the same run (migration 015).
  // Not a new search: the hourly limit is not touched; the function allows one
  // retry per chunk, only for the latest run and within its hour.
  async function retryChunk(index) {
    setLoading(true)
    setError(null)
    setChunks((current) => current.map((chunk, i) => (i === index ? { ...chunk, status: 'running', retried: true } : chunk)))
    const { data, error: retryError } = await supabase.functions.invoke('search', {
      body: { action: 'retry', run_id: chunkRunId, chunk: index },
    })
    const fields = retryError
      ? { status: 'error', message: await describeChunkError(retryError) }
      : { status: 'done', count: data.results.length, stoppedEarly: data.stopped_early }
    setChunks((current) => current.map((chunk, i) => (i === index ? { ...chunk, ...fields } : chunk)))
    await reloadRun(chunkRunId)
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
        <span className="hint">
          Óránként egy keresés. Városonként egy rész (max. 6, a remote is egy), részenként legfeljebb 4 webes
          keresés (~$0.04; szerb városoknál 3) és kb. 1 perc. Egy hibás rész egyszer újrapróbálható.
        </span>
      </form>

      {chunks.length > 0 && (
        <ol className="chunk-progress" aria-label="Haladás városonként">
          {chunks.map((chunk, index) => (
            <li key={chunk.label} className={`chunk ${chunk.status}`}>
              <strong>{chunk.label}</strong>: {chunkLine(chunk)}
              {chunk.status === 'error' && !chunk.retried && !loading && (
                <button type="button" className="secondary small retry" onClick={() => retryChunk(index)}>
                  Újrapróbálás
                </button>
              )}
            </li>
          ))}
        </ol>
      )}

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
          <ResultCard
            key={result.id}
            result={result}
            applied={result.already_applied || appliedKeys.has(result.company_key)}
          />
        ))}
      </div>
    </div>
  )
}
