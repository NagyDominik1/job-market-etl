// Input and output validation for the "search" Edge Function.
//
// Pure functions, no Deno or npm imports, so the same file runs in the Edge
// Function (Deno) and in the tests (Node: node --test "supabase/functions/search/*.test.ts").
//
// Two directions, both untrusted:
//   - the request body: comes from the browser (anyone with a login could send
//     anything, not just what the form allows)
//   - Claude's results: built from web pages, which can contain anything, even
//     text written to trick an AI ("prompt injection"). So every field is
//     checked, cut to a maximum length, and links must be http(s).

export const MODES = ['jobs', 'companies'] as const
export type Mode = (typeof MODES)[number]

export const SENIORITIES = ['intern', 'junior', 'mid', 'senior', 'lead'] as const

export interface SearchParams {
  countries: string[] // ISO 3166-1 alpha-2, e.g. "HU", "RS"
  cities: string[] // priority order
  remote: boolean // include remote positions / remote-friendly companies
  roles: string[]
  seniority: string[]
  languages: string[] // languages I can work in, e.g. "hungarian"
}

export interface SearchResult {
  kind: 'job' | 'company'
  title: string | null
  company_name: string
  city: string | null
  remote: boolean | null
  url: string | null
  deadline: string | null // YYYY-MM-DD
  contact: string | null
  reasons: string[]
  score: number
}

type Ok<T> = { ok: true; value: T }
type Err = { ok: false; error: string }

// --- small helpers ------------------------------------------------------------------

// Control characters (except normal whitespace) have no place in a title or a
// city and could mess up the log or the page.
const CONTROL_CHARS = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g

/** Trimmed, single-spaced, without control characters, at most `max` chars. */
export function cleanText(value: unknown, max: number): string {
  if (typeof value !== 'string') return ''
  return value.replace(CONTROL_CHARS, '').replace(/\s+/g, ' ').trim().slice(0, max)
}

/** The URL if it is a valid http(s) URL (no javascript:, data:, ftp:...), else null. */
export function httpUrl(value: unknown): string | null {
  const text = cleanText(value, 500)
  if (!text) return null
  try {
    const url = new URL(text)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
    if (url.username || url.password) return null // "https://user:pass@host" tricks
    return url.toString()
  } catch {
    return null
  }
}

/** A real calendar date in YYYY-MM-DD form, else null ("2026-02-30" -> null). */
export function isoDate(value: unknown): string | null {
  const text = cleanText(value, 40) // not cut to 10: "2026-11-30T10:00" must fail, not become a date
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null
  const date = new Date(`${text}T00:00:00Z`)
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === text ? text : null
}

const EMAIL = /^[^\s@<>()"',;]+@[^\s@<>()"',;]+\.[a-z]{2,}$/i

/** A careers contact: an http(s) URL or an e-mail address, else null. */
export function contact(value: unknown): string | null {
  const text = cleanText(value, 200)
  if (!text) return null
  if (EMAIL.test(text)) return text
  return httpUrl(text)
}

function stringList(value: unknown, maxItems: number, maxLength: number): string[] | null {
  if (value === undefined) return []
  if (!Array.isArray(value)) return null
  const items = value.map((item) => cleanText(item, maxLength)).filter((item) => item !== '')
  return [...new Set(items)].slice(0, maxItems)
}

// --- the request ------------------------------------------------------------------------

/** Validate the browser's request body: { mode, params }. */
export function validateRequest(body: unknown): Ok<{ mode: Mode; params: SearchParams }> | Err {
  if (typeof body !== 'object' || body === null) return { ok: false, error: 'The body must be a JSON object' }
  const { mode, params = {} } = body as { mode?: unknown; params?: unknown }

  if (!MODES.includes(mode as Mode)) return { ok: false, error: 'mode must be "jobs" or "companies"' }
  if (typeof params !== 'object' || params === null) return { ok: false, error: 'params must be an object' }
  const p = params as Record<string, unknown>

  const countries = stringList(p.countries, 5, 10) // not cut to 2: "HUN" must fail, not become "HU"
  if (countries === null) return { ok: false, error: 'countries must be a list' }
  const countryCodes = countries.map((code) => code.toUpperCase())
  if (countryCodes.some((code) => !/^[A-Z]{2}$/.test(code))) {
    return { ok: false, error: 'countries must be 2-letter codes, e.g. HU, RS' }
  }

  const cities = stringList(p.cities, 10, 60)
  const roles = stringList(p.roles, 10, 60)
  const languages = stringList(p.languages, 5, 30)
  const seniority = stringList(p.seniority, 5, 10)
  if (cities === null || roles === null || languages === null || seniority === null) {
    return { ok: false, error: 'cities, roles, languages and seniority must be lists' }
  }
  const levels = seniority.map((level) => level.toLowerCase())
  if (levels.some((level) => !(SENIORITIES as readonly string[]).includes(level))) {
    return { ok: false, error: `seniority must be from: ${SENIORITIES.join(', ')}` }
  }
  if (p.remote !== undefined && typeof p.remote !== 'boolean') {
    return { ok: false, error: 'remote must be true or false' }
  }
  if (countryCodes.length === 0 && cities.length === 0 && p.remote !== true) {
    return { ok: false, error: 'Give at least one country, city, or remote' }
  }

  return {
    ok: true,
    value: {
      mode: mode as Mode,
      params: {
        countries: countryCodes,
        cities,
        remote: p.remote === true,
        roles,
        seniority: levels,
        languages,
      },
    },
  }
}

// --- Claude's results --------------------------------------------------------------------

export const MAX_RESULTS = 30

/**
 * Turn the submit_results tool input into rows we can store. Invalid items are
 * dropped (and counted), never "fixed up": a result without a company or with
 * a score outside 0-100 is not trustworthy.
 */
export function validateResults(input: unknown, mode: Mode): { results: SearchResult[]; dropped: number } {
  const items = (input as { results?: unknown } | null)?.results
  if (!Array.isArray(items)) return { results: [], dropped: 0 }

  const results: SearchResult[] = []
  let dropped = 0
  const seen = new Set<string>()

  for (const raw of items.slice(0, MAX_RESULTS * 2)) {
    const item = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>
    const companyName = cleanText(item.company_name, 200)
    const title = mode === 'jobs' ? cleanText(item.title, 200) : ''
    const score = item.score
    const valid =
      companyName !== '' &&
      (mode === 'companies' || title !== '') &&
      typeof score === 'number' && Number.isInteger(score) && score >= 0 && score <= 100

    // The same company/position twice in one answer: keep the first.
    const key = `${companyName.toLowerCase()}|${title.toLowerCase()}`
    if (!valid || seen.has(key) || results.length >= MAX_RESULTS) {
      dropped++
      continue
    }
    seen.add(key)

    results.push({
      kind: mode === 'jobs' ? 'job' : 'company',
      title: title || null,
      company_name: companyName,
      city: cleanText(item.city, 100) || null,
      remote: typeof item.remote === 'boolean' ? item.remote : null,
      url: httpUrl(item.url),
      deadline: mode === 'jobs' ? isoDate(item.deadline) : null,
      contact: mode === 'companies' ? contact(item.contact) : null,
      reasons: stringList(item.reasons, 6, 200) ?? [],
      score: score as number,
    })
  }
  results.sort((a, b) => b.score - a.score)
  return { results, dropped }
}
