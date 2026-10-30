// Tracker data access shared by the Jelentkezéseim tab and the "Hozzáadom"
// buttons on the Job feed and Keresés tabs. The dashboard only INSERTs
// applications and events; the database trigger (migrations 006 + 011) sets
// status and next action, so the follow-up rules live in one place.
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
  return data.id
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
