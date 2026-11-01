import { useState } from 'react'
import { supabase } from './supabase.js'
import { safeHref } from './applications.js'
import { LANGUAGE_LABELS } from './letters.js'
import './tracker.css'
import './letters.css'

// "Levél" on an application or a search result (Phase 9.5): asks the Edge
// Function "draft-letter" for a short letter and shows it with the suggested
// CV and the contact. I always send it myself: "Másolás" copies the text,
// "Megnyitás levelezőben" opens my mail program with it (mailto:). Nothing is
// sent automatically. The text can be edited here before copying.
// The language is always my choice (no automatic guess): the button is
// disabled until hu / sr / en is picked; an application's saved language is
// pre-selected. Claude writes the body; the template is the fallback (body_by).

// Errors from functions.invoke(): our JSON {error} or a plain status.
async function describeLetterError(error) {
  if (error.name === 'FunctionsFetchError') return 'Nem jött válasz a szervertől. Próbáld újra.'
  try {
    const body = await error.context.json()
    if (body?.error) return body.error
  } catch {
    // not our JSON
  }
  return `A levél nem készült el (HTTP ${error.context?.status ?? '?'}).`
}

function mailtoHref(draft, subject, body) {
  const to = draft.contact_is_email ? encodeURIComponent(draft.contact) : ''
  return `mailto:${to}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
}

// target: { application_id } or { search_result_id }
function LetterPanel({ target, defaultLanguage }) {
  const [language, setLanguage] = useState(LANGUAGE_LABELS[defaultLanguage] ? defaultLanguage : '') // '' = not picked yet
  const [draft, setDraft] = useState(null)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [copied, setCopied] = useState(false)

  async function generate(chosen) {
    setBusy(true)
    setError(null)
    setCopied(false)
    const { data, error: invokeError } = await supabase.functions.invoke('draft-letter', {
      body: { ...target, language: chosen },
    })
    if (invokeError) {
      setError(await describeLetterError(invokeError))
    } else {
      setDraft(data)
      setSubject(data.subject)
      setBody(data.body)
    }
    setBusy(false)
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(body)
      setCopied(true)
    } catch {
      setError('A vágólap nem érhető el: jelöld ki és másold a szöveget kézzel.')
    }
  }

  const contactHref = draft?.contact
    ? draft.contact_is_email ? `mailto:${draft.contact}` : safeHref(draft.contact)
    : null

  return (
    <div className="letter-panel box">
      <div className="letter-controls">
        <label>
          Nyelv
          <select value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="" disabled>válassz…</option>
            {Object.entries(LANGUAGE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => generate(language)} disabled={busy || !language}>
          {busy ? 'Írás…' : draft ? 'Újraírás' : 'Levél írása'}
        </button>
      </div>
      {error && <p className="status error">{error}</p>}

      {draft && (
        <>
          <p className="letter-meta">
            {LANGUAGE_LABELS[draft.language]} levél · Önéletrajz:{' '}
            <strong>{draft.cv_variant === 'hu' ? 'magyar' : 'angol'}</strong>
            {draft.body_by === 'template' &&
              ' · a levél a sablonodból készült (Claude nem volt elérhető, vagy kétszer sem felelt meg az ellenőrzésen: nyelv, hossz, ismétlés, klisé, kitalált tapasztalat vagy projekt)'}
          </p>
          {draft.company_summary && (
            <p className="letter-meta">
              A cégről{draft.company_by === 'fetched' ? ' (most kiolvasva a cég oldaláról, elmentve)' : ''}:{' '}
              {draft.company_summary}
            </p>
          )}
          {draft.requirements?.length > 0 && (
            <p className="letter-meta">
              A hirdetés fő követelményei{draft.facts_by === 'fetched' ? ' (most kiolvasva a hirdetésből, elmentve)' : ''}:{' '}
              {draft.requirements.join(' · ')}
            </p>
          )}
          <p className="letter-meta">
            Kapcsolat:{' '}
            {contactHref ? (
              <a href={contactHref} target="_blank" rel="noopener noreferrer">{draft.contact}</a>
            ) : (
              'nincs ismert cím (nézd meg a hirdetést vagy a cég oldalát)'
            )}
          </p>
          <label className="letter-field">
            Tárgy
            <input value={subject} onChange={(e) => setSubject(e.target.value)} />
          </label>
          <label className="letter-field">
            Szöveg
            <textarea rows="9" value={body} onChange={(e) => setBody(e.target.value)} />
          </label>
          <div className="form-buttons">
            <button type="button" onClick={copy}>{copied ? 'Kimásolva' : 'Másolás'}</button>
            <a className="button secondary" href={mailtoHref(draft, subject, body)}>Megnyitás levelezőben</a>
          </div>
        </>
      )}
    </div>
  )
}

export default function LetterButton({ target, defaultLanguage = null }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" className="link" onClick={() => setOpen(!open)}>
        {open ? 'Levél elrejtése' : 'Levél'}
      </button>
      {open && <LetterPanel target={target} defaultLanguage={defaultLanguage} />}
    </>
  )
}
