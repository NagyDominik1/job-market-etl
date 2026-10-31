import { useCallback, useEffect, useState } from 'react'
import { supabase } from './supabase.js'
import { cleanSearch, useDebounced } from './JobFeed.jsx'
import {
  EVENT_BUTTONS, EVENT_LABELS, STATUS_LABELS,
  addEvent, createApplication, describeError, findDuplicates, formatDate, safeHref,
} from './applications.js'
import './tracker.css'

// The columns the form edits (011 allows INSERT/UPDATE on exactly these,
// minus job_id, which only the Hozzáadom button sets).
const EMPTY_FORM = {
  company_name: '', position: '', city: '', posting_url: '', deadline: '',
  language: '', cv_variant: '', notes: '',
}

// Empty inputs become NULL (an empty string is not "no deadline").
function toRow(form) {
  return Object.fromEntries(Object.entries(form).map(([key, value]) => [key, value.trim() === '' ? null : value.trim()]))
}

function toForm(app) {
  return Object.fromEntries(Object.keys(EMPTY_FORM).map((key) => [key, app[key] ?? '']))
}

function ApplicationForm({ initial, submitLabel, onSubmit, onCancel }) {
  const [form, setForm] = useState(initial)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const field = (key) => ({
    value: form[key],
    onChange: (event) => setForm({ ...form, [key]: event.target.value }),
  })

  async function submit(event) {
    event.preventDefault()
    if (form.posting_url.trim() && !safeHref(form.posting_url.trim())) {
      setError('A link http:// vagy https:// kezdetű legyen.') // the database checks this too
      return
    }
    setBusy(true)
    setError(null)
    try {
      await onSubmit(toRow(form))
    } catch (err) {
      setError(describeError(err))
    }
    setBusy(false)
  }

  return (
    <form className="filters app-form" onSubmit={submit}>
      <label>Cég *<input required {...field('company_name')} /></label>
      <label>Pozíció (üres = nyílt jelentkezés)<input {...field('position')} /></label>
      <label>Város<input {...field('city')} /></label>
      <label>Link<input type="url" placeholder="https://…" {...field('posting_url')} /></label>
      <label>Határidő<input type="date" {...field('deadline')} /></label>
      <label>
        Levél nyelve
        <select {...field('language')}>
          <option value="">–</option>
          <option value="hu">magyar</option>
          <option value="sr">szerb</option>
          <option value="en">angol</option>
        </select>
      </label>
      <label>
        Önéletrajz
        <select {...field('cv_variant')}>
          <option value="">–</option>
          <option value="hu">magyar</option>
          <option value="en">angol</option>
        </select>
      </label>
      <label className="wide">Jegyzet<textarea rows="2" {...field('notes')} /></label>
      <div className="form-buttons wide">
        <button type="submit" disabled={busy}>{busy ? 'Mentés…' : submitLabel}</button>
        <button type="button" className="secondary" onClick={onCancel}>Mégse</button>
      </div>
      {error && <p className="status error wide">{error}</p>}
    </form>
  )
}

// "Új jelentkezés": the same duplicate check as the Hozzáadom button.
function NewApplication({ onDone }) {
  const [pending, setPending] = useState(null) // { row, duplicates } while asking
  const [error, setError] = useState(null)

  async function create(row) {
    await createApplication(row)
    onDone(true)
  }

  async function submit(row) {
    const duplicates = await findDuplicates(row.company_name)
    if (duplicates.length) setPending({ row, duplicates })
    else await create(row)
  }

  if (pending) {
    return (
      <div className="duplicate-warning box">
        <p>
          Már van jelentkezésed ennél a cégnél:{' '}
          {pending.duplicates
            .map((d) => `${d.company_name}${d.position ? ` – ${d.position}` : ''} (${STATUS_LABELS[d.status]})`)
            .join('; ')}
        </p>
        <button onClick={() => create(pending.row).catch((err) => setError(describeError(err)))}>Mégis hozzáadom</button>
        <button className="secondary" onClick={() => setPending(null)}>Vissza</button>
        {error && <p className="status error">{error}</p>}
      </div>
    )
  }
  return <ApplicationForm initial={EMPTY_FORM} submitLabel="Hozzáadom" onSubmit={submit} onCancel={() => onDone(false)} />
}

// The small form behind an event button: when it happened (empty = today)
// plus the text or date the event needs.
function EventForm({ button, onSave, onCancel }) {
  const [occurredAt, setOccurredAt] = useState('')
  const [body, setBody] = useState('')
  const [statedDate, setStatedDate] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await onSave({ occurredAt, body: body.trim(), statedDate })
    } catch (err) {
      setError(describeError(err))
      setBusy(false)
    }
  }

  return (
    <form className="event-form" onSubmit={submit}>
      <strong>{button.label}</strong>
      <label>
        Mikor történt (üres = ma)
        <input type="date" value={occurredAt} onChange={(e) => setOccurredAt(e.target.value)} />
      </label>
      {button.input === 'text' && (
        <label>
          {button.type === 'note' ? 'Megjegyzés' : 'Mit írtak?'}
          <textarea rows="2" required={button.required} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>
      )}
      {button.input === 'date' && (
        <label>
          {button.type === 'interview' ? 'Interjú időpontja (nem kötelező)' : 'Melyik napot mondták?'}
          <input type="date" required={button.required} value={statedDate} onChange={(e) => setStatedDate(e.target.value)} />
        </label>
      )}
      <div className="form-buttons">
        <button type="submit" disabled={busy}>{busy ? 'Mentés…' : 'Mentés'}</button>
        <button type="button" className="secondary" onClick={onCancel}>Mégse</button>
      </div>
      {error && <p className="status error">{error}</p>}
    </form>
  )
}

function History({ applicationId, version }) {
  const [events, setEvents] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let ignore = false
    supabase
      .from('application_events')
      .select('*')
      .eq('application_id', applicationId)
      .order('id') // insertion order, the order the trigger used
      .then(({ data, error }) => {
        if (ignore) return
        if (error) setError(error.message)
        else setEvents(data)
      })
    return () => {
      ignore = true
    }
  }, [applicationId, version])

  if (error) return <p className="status error">{error}</p>
  if (!events) return <p className="status">Betöltés…</p>
  if (!events.length) return <p className="status">Még nincs esemény.</p>
  return (
    <ol className="history">
      {events.map((e) => (
        <li key={e.id}>
          <span className="history-date">{formatDate(e.occurred_at)}</span> {EVENT_LABELS[e.event_type] ?? e.event_type}
          {e.stated_date && ` – ${formatDate(e.stated_date)}`}
          {e.body && <span className="history-body">{e.body}</span>}
        </li>
      ))}
    </ol>
  )
}

function ApplicationCard({ app, onChanged }) {
  const [open, setOpen] = useState(false) // history visible
  const [editing, setEditing] = useState(false)
  const [eventButton, setEventButton] = useState(null) // the event form being filled in
  const [version, setVersion] = useState(0) // bumped after a new event: reloads the history
  const href = safeHref(app.posting_url)
  const finished = ['rejected', 'no_response', 'offer'].includes(app.status)

  async function saveEvent(fields) {
    await addEvent(app.id, eventButton.type, fields)
    setEventButton(null)
    setVersion((v) => v + 1)
    onChanged()
  }

  async function saveEdit(row) {
    const { error } = await supabase.from('applications').update(row).eq('id', app.id)
    if (error) throw error
    setEditing(false)
    onChanged()
  }

  return (
    <article className={`card application${finished ? ' finished' : ''}`}>
      <header className="app-head">
        <div>
          <h2>
            {app.company_name}
            <span className={`badge status-${app.status}`}>{STATUS_LABELS[app.status]}</span>
          </h2>
          <p className="meta">
            {app.position || 'Nyílt jelentkezés'}
            {app.city && ` · ${app.city}`}
            {href && (
              <>
                {' · '}
                <a href={href} target="_blank" rel="noopener noreferrer">hirdetés</a>
              </>
            )}
          </p>
        </div>
      </header>

      <p className="card-foot">
        {app.applied_at && <span>Elküldve: {formatDate(app.applied_at)}</span>}
        {app.deadline && <span>Határidő: {formatDate(app.deadline)}</span>}
        {app.next_action_date && (
          <span className="next-action">
            Következő lépés {formatDate(app.next_action_date)}: {app.next_action_reason}
          </span>
        )}
      </p>
      {app.notes && <p className="notes">{app.notes}</p>}

      <div className="event-buttons">
        {EVENT_BUTTONS.map((button) => (
          <button key={button.type} className="secondary small" onClick={() => setEventButton(button)}>
            {button.label}
          </button>
        ))}
      </div>
      {eventButton && <EventForm key={eventButton.type} button={eventButton} onSave={saveEvent} onCancel={() => setEventButton(null)} />}

      <div className="card-links">
        <button className="link" onClick={() => setOpen(!open)}>{open ? 'Előzmények elrejtése' : 'Előzmények'}</button>
        <button className="link" onClick={() => setEditing(!editing)}>Adatok szerkesztése</button>
      </div>
      {editing && <ApplicationForm initial={toForm(app)} submitLabel="Mentés" onSubmit={saveEdit} onCancel={() => setEditing(false)} />}
      {open && <History applicationId={app.id} version={version} />}
    </article>
  )
}

// The list's order. "Legújabb elöl": by applied_at, newest first; applications
// not sent yet (applied_at NULL) come first, newest saved first (created_at):
// they are the freshest entries and the ones still waiting for me.
const SORTS = {
  newest: (query) => query
    .order('applied_at', { ascending: false, nullsFirst: true })
    .order('created_at', { ascending: false })
    .order('id', { ascending: false }),
  // nullsFirst: false -> applications without a next action go last.
  next: (query) => query
    .order('next_action_date', { ascending: true, nullsFirst: false })
    .order('id'),
}

export default function TrackerTab({ session }) {
  const [status, setStatus] = useState('')
  const [sort, setSort] = useState('newest')
  const [city, setCity] = useState('')
  const [adding, setAdding] = useState(false)
  const [apps, setApps] = useState([])
  const [today, setToday] = useState([])
  const [expiring, setExpiring] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [reload, setReload] = useState(0)

  const debouncedCity = useDebounced(city)
  const refresh = useCallback(() => setReload((n) => n + 1), [])

  useEffect(() => {
    let ignore = false

    async function load() {
      setLoading(true)
      setError(null)
      // Filtering and sorting in the database, like the Job feed.
      let query = SORTS[sort](supabase.from('applications').select('*'))
      if (status) query = query.eq('status', status)
      const cityText = cleanSearch(debouncedCity)
      if (cityText) query = query.ilike('city', `%${cityText}%`)

      const [appsRes, todayRes, expiringRes] = await Promise.all([
        query,
        supabase.from('todays_actions').select('*'),
        supabase.from('expiring').select('*'),
      ])
      if (ignore) return
      const failed = appsRes.error ?? todayRes.error ?? expiringRes.error
      if (failed) setError(failed.message)
      setApps(appsRes.data ?? [])
      setToday(todayRes.data ?? [])
      setExpiring(expiringRes.data ?? [])
      setLoading(false)
    }

    load()
    return () => {
      ignore = true
    }
  }, [status, sort, debouncedCity, reload])

  return (
    <div className="page">
      <header className="topbar">
        <h1>Jelentkezéseim</h1>
        <span className="who">{session.user.email}</span>
        <button className="secondary" onClick={() => supabase.auth.signOut()}>
          Log out
        </button>
      </header>

      {error && <p className="status error">Nem sikerült betölteni: {error}</p>}

      <section className="todo-lists">
        <div className="box">
          <h2>Ma teendő</h2>
          {today.length === 0 ? (
            <p className="status">Ma nincs teendő.</p>
          ) : (
            <ul>
              {today.map((row) => (
                <li key={row.id}>
                  <strong>{row.company_name}</strong>
                  {row.position && ` – ${row.position}`}: {row.next_action_reason}
                  {row.days_overdue > 0 && <span className="overdue"> ({row.days_overdue} napja esedékes)</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="box">
          <h2>Hamarosan lejár</h2>
          {expiring.length === 0 ? (
            <p className="status">Nincs közeli határidő.</p>
          ) : (
            <ul>
              {expiring.map((row) => (
                <li key={row.id}>
                  <strong>{row.company_name}</strong>
                  {row.position && ` – ${row.position}`}: {formatDate(row.deadline)}{' '}
                  <span className={row.days_left < 0 ? 'overdue' : ''}>
                    ({row.days_left < 0 ? 'lejárt' : row.days_left === 0 ? 'ma' : `${row.days_left} nap`})
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="filters tracker-filters" aria-label="Szűrők">
        <label>
          Állapot
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Mind</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          Rendezés
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="newest">Legújabb elöl</option>
            <option value="next">Következő teendő szerint</option>
          </select>
        </label>
        <label>
          Város
          <input type="search" placeholder="pl. Szeged" value={city} onChange={(e) => setCity(e.target.value)} />
        </label>
        {!adding && <button onClick={() => setAdding(true)}>Új jelentkezés</button>}
      </section>

      {adding && (
        <NewApplication
          onDone={(created) => {
            setAdding(false)
            if (created) refresh()
          }}
        />
      )}

      <p className="status">{loading ? 'Betöltés…' : `${apps.length} jelentkezés`}</p>
      <div className="cards">
        {apps.map((app) => (
          <ApplicationCard key={app.id} app={app} onChanged={refresh} />
        ))}
      </div>
    </div>
  )
}
