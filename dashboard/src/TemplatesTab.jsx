import { useEffect, useState } from 'react'
import { supabase } from './supabase.js'
import { describeError } from './applications.js'
import {
  KIND_LABELS, LANGUAGE_LABELS, MAX_SENTENCES, PLACEHOLDERS, templateSentences, unknownPlaceholders,
} from './letters.js'
import './tracker.css'
import './letters.css'

// "Sablonok" (Phase 9.5): edit the six letter templates (hu/sr/en x posting/
// open). Migration 016 lets the dashboard read, add and edit the text; no
// delete. A missing template is added on the first save. Checked before
// saving: only known placeholders, at most 5 sentences ({tailored_line} = 1).

const COMBINATIONS = Object.keys(LANGUAGE_LABELS).flatMap((language) =>
  Object.keys(KIND_LABELS).map((kind) => ({ language, kind })))

/** Why a template can't be saved, or null. */
function problem(subject, body) {
  if (!subject.trim() || !body.trim()) return 'A tárgy és a szöveg nem lehet üres.'
  const unknown = unknownPlaceholders(`${subject} ${body}`)
  if (unknown.length) return `Ismeretlen helyőrző: {${unknown.join('}, {')}}`
  const sentences = templateSentences(body)
  if (sentences > MAX_SENTENCES) return `${sentences} mondat: legfeljebb ${MAX_SENTENCES} lehet ({tailored_line} is egy mondat).`
  return null
}

function TemplateCard({ language, kind, row, onSaved }) {
  const [subject, setSubject] = useState(row?.subject ?? '')
  const [body, setBody] = useState(row?.body ?? '')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null) // { error: bool, text }
  const invalid = problem(subject, body)
  const changed = subject !== (row?.subject ?? '') || body !== (row?.body ?? '')

  async function save() {
    setBusy(true)
    setMessage(null)
    // Edit = UPDATE of the text only (language/kind are fixed, 016); a missing template = INSERT.
    const { error } = row
      ? await supabase.from('letter_templates').update({ subject, body }).eq('id', row.id)
      : await supabase.from('letter_templates').insert({ language, kind, subject, body })
    setBusy(false)
    if (error) {
      setMessage({ error: true, text: describeError(error) })
    } else {
      setMessage({ error: false, text: 'Mentve.' })
      onSaved()
    }
  }

  return (
    <article className="card template-card">
      <h2>
        {LANGUAGE_LABELS[language]} · {KIND_LABELS[kind]}
        {!row && <span className="badge applied">hiányzik</span>}
      </h2>
      <label className="letter-field">
        Tárgy
        <input value={subject} onChange={(e) => setSubject(e.target.value)} />
      </label>
      <label className="letter-field">
        Szöveg ({templateSentences(body)} / {MAX_SENTENCES} mondat)
        <textarea rows="8" value={body} onChange={(e) => setBody(e.target.value)} />
      </label>
      {invalid && changed && <p className="status error">{invalid}</p>}
      {!body.includes('{tailored_line}') && body.trim() && (
        <p className="status notice">Nincs benne {'{tailored_line}'}: a levél nem lesz személyre szabva.</p>
      )}
      <div className="form-buttons">
        <button type="button" onClick={save} disabled={busy || !changed || Boolean(invalid)}>
          {busy ? 'Mentés…' : 'Mentés'}
        </button>
        {message && <span className={message.error ? 'status error' : 'status'}>{message.text}</span>}
      </div>
    </article>
  )
}

export default function TemplatesTab({ session }) {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let ignore = false
    supabase.from('letter_templates').select('*').then(({ data, error }) => {
      if (ignore) return
      if (error) setError(error.message)
      else setRows(data)
    })
    return () => {
      ignore = true
    }
  }, [reload])

  const find = (language, kind) => rows?.find((r) => r.language === language && r.kind === kind)

  return (
    <div className="page">
      <header className="topbar">
        <h1>Sablonok</h1>
        <span className="who">{session.user.email}</span>
        <button className="secondary" onClick={() => supabase.auth.signOut()}>
          Log out
        </button>
      </header>

      <p className="template-help">
        Helyőrzők: {PLACEHOLDERS.map((name) => `{${name}}`).join(', ')}. A {'{skills}'} helyére a tudásod kerül (a
        hirdetésben kértek elöl, legfeljebb 4), a {'{tailored_line}'} helyére egy, a hirdetéshez írt mondat a levél
        nyelvén. Magyarul írd így: „a {'{company}'}”, „A {'{position}'}”: a/az a szó alapján automatikus. Legfeljebb{' '}
        {MAX_SENTENCES} mondat; a megszólítás és az elköszönés nem számít. Név és telefonszám helyett az e-mail
        aláírásod kerül a végére.
      </p>
      {error && <p className="status error">Nem sikerült betölteni: {error}</p>}
      {!rows && !error && <p className="status">Betöltés…</p>}
      {rows && (
        <div className="cards">
          {COMBINATIONS.map(({ language, kind }) => {
            const row = find(language, kind)
            return (
              <TemplateCard
                key={`${language}-${kind}`}
                language={language}
                kind={kind}
                row={row}
                onSaved={() => setReload((n) => n + 1)}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}
