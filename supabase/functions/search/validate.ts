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
//     checked, cut to a maximum length, and links must be http(s). Job results
//     must also link to one posting (not a list) and pass the profile's
//     dealbreakers: the code enforces them, the prompt alone is not enough.

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

// --- posting URLs ------------------------------------------------------------------------

// A job result must link to ONE posting, not to a list of postings. These are
// the usual shapes of list pages; a heuristic, so the main check is still that
// Claude opened the page (confirmed). Both together catch most mistakes.

// ?q=php, ?keyword=..., ?page=2: a search or a paged list.
const LISTING_QUERY_KEYS = new Set([
  'q', 'query', 'keyword', 'keywords', 'search', 's', 'kw', 'term', 'text', 'page',
  'category', 'kategoria', 'kategorija', 'kulcsszo', 'pretraga',
])
// A path segment that marks a search or category page wherever it appears.
const LISTING_SEGMENTS = new Set([
  'search', 'kereses', 'pretraga', 'results', 'category', 'categories', 'kategoria', 'kategorija',
  'tag', 'tags', 'allasok', 'allasajanlatok', 'oglasi', 'oglasi-za-posao',
])
// The LAST path segment of an index page ("/careers", "/jobs/").
const INDEX_SEGMENTS = new Set([
  'jobs', 'careers', 'career', 'karrier', 'karriera', 'kariera', 'karijera', 'poslovi', 'posao',
  'openings', 'positions', 'open-positions', 'vacancies', 'konkursi', 'job-openings',
])
// ATS hosts where /<company> alone is the company's job list.
const ATS_HOSTS = new Set([
  'boards.greenhouse.io', 'job-boards.greenhouse.io', 'jobs.lever.co', 'apply.workable.com',
  'jobs.smartrecruiters.com', 'jobs.ashbyhq.com',
])

/** True when the URL looks like a list of postings (search, category, careers index). */
export function isListingUrl(value: string): boolean {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    return true // not a URL at all: certainly not a posting
  }
  const segments = url.pathname.split('/').filter((s) => s !== '').map((s) => s.toLowerCase())
  if (segments.length === 0) return true // a homepage
  if ([...url.searchParams.keys()].some((key) => LISTING_QUERY_KEYS.has(key.toLowerCase()))) return true
  if (segments.some((s) => LISTING_SEGMENTS.has(s))) return true
  if (INDEX_SEGMENTS.has(segments[segments.length - 1])) return true
  if (ATS_HOSTS.has(url.hostname.toLowerCase()) && segments.length === 1) return true
  return false
}

// --- profile rules for job results -------------------------------------------------------

/** The dealbreakers the code enforces itself (the prompt asks for them too). */
export interface ResultRules {
  allowedSeniority: string[] // from the profile; empty = any level
  minYearsRequired: number | null // drop postings asking for this many years or more
  titleKeywords: string[] // drop titles containing one of these (lowercase)
}

export const NO_RULES: ResultRules = { allowedSeniority: [], minYearsRequired: null, titleKeywords: [] }

/** ResultRules from the search_profile row (null or missing fields = no rule). */
export function rulesFromProfile(profile: { seniority?: unknown; dealbreakers?: unknown } | null): ResultRules {
  if (!profile) return NO_RULES
  const dealbreakers = (typeof profile.dealbreakers === 'object' && profile.dealbreakers !== null
    ? profile.dealbreakers
    : {}) as Record<string, unknown>
  const years = dealbreakers.min_years_required
  return {
    allowedSeniority: (stringList(profile.seniority, 5, 10) ?? []).map((level) => level.toLowerCase()),
    minYearsRequired: typeof years === 'number' && years > 0 ? years : null,
    titleKeywords: (stringList(dealbreakers.title_keywords, 50, 60) ?? []).map((word) => word.toLowerCase()),
  }
}

// Seniority words in a title, per level ("Senior PHP Developer", "Szenior
// fejlesztő", "Sr. Backend", "Tech Lead", "Vezető fejlesztő"). \p{L} instead
// of \b, so accented letters count as letters.
const TITLE_LEVEL_WORDS: Record<string, RegExp> = {
  senior: /(?<![\p{L}\p{N}])(senior|szenior|sr|principal)(?![\p{L}\p{N}])/iu,
  lead: /(?<![\p{L}\p{N}])(lead|vezető|vezeto)(?![\p{L}\p{N}])/iu,
}

/** Why a job result breaks a profile rule, or null when it is fine. */
export function dealbreaker(
  item: { title: string; seniority: unknown; years_required: unknown },
  rules: ResultRules,
): string | null {
  const allowed = rules.allowedSeniority
  if (allowed.length > 0) {
    const level = typeof item.seniority === 'string' ? item.seniority.toLowerCase() : 'unknown'
    if (level !== 'unknown' && (SENIORITIES as readonly string[]).includes(level) && !allowed.includes(level)) {
      return `seniority ${level}`
    }
    for (const [titleLevel, pattern] of Object.entries(TITLE_LEVEL_WORDS)) {
      if (!allowed.includes(titleLevel) && pattern.test(item.title)) return `${titleLevel} in the title`
    }
  }
  const years = item.years_required
  if (rules.minYearsRequired !== null && typeof years === 'number' && years >= rules.minYearsRequired) {
    return `requires ${years} years`
  }
  const title = item.title.toLowerCase()
  const keyword = rules.titleKeywords.find((word) => title.includes(word))
  return keyword ? `title contains "${keyword}"` : null
}

// --- Claude's results --------------------------------------------------------------------

export const MAX_RESULTS = 30

/** Why results were dropped, counted per reason (logged and returned to the page). */
export interface DroppedBy {
  invalid: number // no company/title, bad score, not an object
  duplicate: number
  over_limit: number
  unconfirmed: number // jobs: Claude did not open the posting page
  listing_url: number // jobs: no URL, or a search/category/careers-index URL
  dealbreaker: number // jobs: seniority, years or title keyword from the profile
}

/**
 * Turn the submit_results tool input into rows we can store. Invalid items are
 * dropped (and counted), never "fixed up": a result without a company or with
 * a score outside 0-100 is not trustworthy. Job results must also be confirmed
 * from the posting itself, link to that posting, and pass the profile rules.
 */
export function validateResults(
  input: unknown,
  mode: Mode,
  rules: ResultRules = NO_RULES,
): { results: SearchResult[]; dropped: number; droppedBy: DroppedBy } {
  const droppedBy: DroppedBy = { invalid: 0, duplicate: 0, over_limit: 0, unconfirmed: 0, listing_url: 0, dealbreaker: 0 }
  const results: SearchResult[] = []
  const done = () => {
    results.sort((a, b) => b.score - a.score)
    const dropped = Object.values(droppedBy).reduce((sum, n) => sum + n, 0)
    return { results, dropped, droppedBy }
  }

  const items = (input as { results?: unknown } | null)?.results
  if (!Array.isArray(items)) return done()
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
    if (!valid) {
      droppedBy.invalid++
      continue
    }

    // The same company/position twice in one answer: keep the first.
    const key = `${companyName.toLowerCase()}|${title.toLowerCase()}`
    if (seen.has(key)) {
      droppedBy.duplicate++
      continue
    }
    seen.add(key)

    const url = httpUrl(item.url)
    if (mode === 'jobs') {
      if (item.confirmed !== true) {
        droppedBy.unconfirmed++
        continue
      }
      if (url === null || isListingUrl(url)) {
        droppedBy.listing_url++
        continue
      }
      if (dealbreaker({ title, seniority: item.seniority, years_required: item.years_required }, rules)) {
        droppedBy.dealbreaker++
        continue
      }
    }
    if (results.length >= MAX_RESULTS) {
      droppedBy.over_limit++
      continue
    }

    results.push({
      kind: mode === 'jobs' ? 'job' : 'company',
      title: title || null,
      company_name: companyName,
      city: cleanText(item.city, 100) || null,
      remote: typeof item.remote === 'boolean' ? item.remote : null,
      url,
      deadline: mode === 'jobs' ? isoDate(item.deadline) : null,
      contact: mode === 'companies' ? contact(item.contact) : null,
      reasons: stringList(item.reasons, 6, 200) ?? [],
      score: score as number,
    })
  }
  return done()
}
