// Tracker data access shared by the Jelentkezéseim tab and the "Hozzáadom"
// buttons on the Job feed and Keresés tabs. The dashboard only INSERTs
// applications and events; the database trigger (migrations 006 + 011) sets
// status and next action, so the follow-up rules live in one place.
import { useEffect, useState } from 'react'
import { supabase } from './supabase.js'

export const STATUS_LABELS = {
  saved: 'Mentve',
  sent: 'Elküldve',
  in_progress: 'Folyamatban',
  rejected: 'Elutasítva',
  no_response: 'Nem válaszoltak',
  offer: 'Ajánlat',
}

// The event buttons, in the order they usually happen. `input` = what the
// form asks for besides the date: 'text' -> body, 'date' -> stated_date
// (required for stated_date, optional for an interview's time).
export const EVENT_BUTTONS = [
  { type: 'sent', label: 'Elküldtem' },
  { type: 'reply', label: 'Válasz jött', input: 'text' },
  { type: 'forwarded', label: 'Továbbították' },
  { type: 'stated_date', label: 'Konkrét dátumot mondtak', input: 'date', required: true },
  { type: 'interview', label: 'Interjú', input: 'date' },
  { type: 'follow_up_sent', label: 'Emlékeztetőt küldtem' },
  { type: 'rejection', label: 'Elutasítás' },
  { type: 'note', label: 'Megjegyzés', input: 'text', required: true },
]

export const EVENT_LABELS = {
  saved: 'Mentve',
  offer: 'Ajánlat',
  ...Object.fromEntries(EVENT_BUTTONS.map((button) => [button.type, button.label])),
}

/** Only http(s) links become <a href> (a "javascript:" link would run code). */
export function safeHref(url) {
  return typeof url === 'string' && /^https?:\/\//i.test(url) ? url : null
}

export function formatDate(value) {
  return value ? new Date(value).toLocaleDateString('hu-HU') : null
}

/**
 * Applications at the same company, whatever the spelling: the database
 * computes the company_key ("ErdSoft Kft." = "ERDSOFT d.o.o." = "erdsoft"),
 * the same function as the generated column, so there is no JS copy to drift.
 */
export async function findDuplicates(companyName) {
  const { data: key, error } = await supabase.rpc('normalize_company_key', { name: companyName })
  if (error) throw error
  const { data, error: listError } = await supabase
    .from('applications')
    .select('id, company_name, position, status')
    .eq('company_key', key)
    .order('id')
  if (listError) throw listError
  return data
}

/**
 * New application (status 'saved' by default), then a 'saved' event: the
 * trigger turns it into "send it 2 days before the deadline" and the history
 * starts with it. Two requests, not one transaction: if the second fails the
 * application still exists, just without a reminder (the error is shown).
 */
export async function createApplication(fields) {
  const { data, error } = await supabase.from('applications').insert(fields).select('id').single()
  if (error) throw error
  await addEvent(data.id, 'saved')
  notifyApplicationsChanged()
  return data.id
}

// "Már jelentkeztél" everywhere (Part A of 9.5): the tabs ask the database
// again when an application is added anywhere on the page. A plain browser
// event, no state library: the tabs only need to know "something changed".
const CHANGED_EVENT = 'applications-changed'

export function notifyApplicationsChanged() {
  window.dispatchEvent(new Event(CHANGED_EVENT))
}

/** A number that goes up whenever an application is added (use it as an effect dependency). */
export function useApplicationsVersion() {
  const [version, setVersion] = useState(0)
  useEffect(() => {
    const bump = () => setVersion((v) => v + 1)
    window.addEventListener(CHANGED_EVENT, bump)
    return () => window.removeEventListener(CHANGED_EVENT, bump)
  }, [])
  return version
}

/**
 * The company_keys of all my applications, as a Set (search results carry
 * their own company_key, 007). Keys are computed by the database; the browser
 * only compares them. Empty while loading or if the request fails: the badge
 * is a hint, not a lock (the duplicate warning still asks the database).
 */
export function useAppliedCompanyKeys() {
  const version = useApplicationsVersion()
  const [keys, setKeys] = useState(() => new Set())
  useEffect(() => {
    let ignore = false
    supabase
      .from('applications')
      .select('company_key')
      .then(({ data }) => {
        if (!ignore && data) setKeys(new Set(data.map((row) => row.company_key)))
      })
    return () => {
      ignore = true
    }
  }, [version])
  return keys
}

/**
 * Which of these pipeline jobs are at a company I applied to (view
 * job_applied, migration 014: same company_key or linked by job_id).
 * Returns a Set of job ids.
 */
export function useAppliedJobIds(jobIds) {
  const version = useApplicationsVersion()
  const [applied, setApplied] = useState(() => new Set())
  const idsKey = jobIds.join(',') // a stable dependency (a new array every render otherwise)
  useEffect(() => {
    let ignore = false
    if (!idsKey) {
      setApplied(new Set())
      return undefined
    }
    supabase
      .from('job_applied')
      .select('job_id')
      .in('job_id', idsKey.split(',').map(Number))
      .then(({ data }) => {
        if (!ignore && data) setApplied(new Set(data.map((row) => row.job_id)))
      })
    return () => {
      ignore = true
    }
  }, [idsKey, version])
  return applied
}

/** One application_events row; the trigger does the rest. */
export async function addEvent(applicationId, eventType, { occurredAt, body, statedDate } = {}) {
  const row = { application_id: applicationId, event_type: eventType }
  if (occurredAt) row.occurred_at = occurredAt // empty = today (Budapest), the column default
  if (body) row.body = body
  if (statedDate) row.stated_date = statedDate
  const { error } = await supabase.from('application_events').insert(row)
  if (error) throw error
}

/** Hungarian text for the error codes a user can actually cause. */
export function describeError(error) {
  if (error?.code === '23514') return 'Érvénytelen adat (a link http:// vagy https:// kezdetű legyen).'
  if (error?.code === '42501') return 'Nincs jogosultság (be vagy jelentkezve?).'
  return error?.message ?? String(error)
}
