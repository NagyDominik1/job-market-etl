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
//     The score and reasons are computed here too (score.ts), never by Claude.

import { EMPTY_CONTEXT, type ScoringContext, scoreCompany, scoreJob } from './score.ts'

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

/** One request to the function: start a run, or run one of its chunks (run 9). */
export type Action =
  | { action: 'start'; mode: Mode; params: SearchParams }
  | { action: 'chunk' | 'retry'; runId: number; chunk: number }

/**
 * {action: "start", mode, params} or {action: "chunk", run_id, chunk}. A body
 * without action is a start (the dashboard before chunks). A chunk call
 * carries no params: the function reads them from the run row, so the
 * browser can't change what a chunk searches.
 */
export function validateAction(body: unknown): Ok<Action> | Err {
  if (typeof body !== 'object' || body === null) return { ok: false, error: 'The body must be a JSON object' }
  const { action } = body as { action?: unknown }
  // retry (9.5): a failed chunk once more, inside the same run (015).
  if (action === 'chunk' || action === 'retry') {
    const { run_id: runId, chunk } = body as { run_id?: unknown; chunk?: unknown }
    if (!Number.isSafeInteger(runId) || (runId as number) <= 0) return { ok: false, error: 'run_id must be a positive integer' }
    if (!Number.isSafeInteger(chunk) || (chunk as number) < 0 || (chunk as number) > 100) {
      return { ok: false, error: 'chunk must be an integer from 0' }
    }
    return { ok: true, value: { action, runId: runId as number, chunk: chunk as number } }
  }
  if (action !== undefined && action !== 'start') return { ok: false, error: 'action must be "start", "chunk" or "retry"' }
  const request = validateRequest(body)
  return request.ok ? { ok: true, value: { action: 'start', ...request.value } } : request
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

// Sites whose terms forbid automated access: web_fetch never opens them
// (blocked_domains in prompt.ts), so a job result linking there can't have been
// confirmed from the posting. Subdomains count too (www., hu.linkedin.com).
export const FETCH_BLOCKED_DOMAINS = ['linkedin.com']

/** True when the URL is on a domain web_fetch is not allowed to open. */
export function isFetchBlocked(value: string): boolean {
  try {
    const host = new URL(value).hostname.toLowerCase()
    return FETCH_BLOCKED_DOMAINS.some((domain) => host === domain || host.endsWith(`.${domain}`))
  } catch {
    return false
  }
}

/**
 * One form per page, for "was this page fetched?": no #fragment, no trailing
 * slash, no "www.", lowercase host (the URL class does that). Null if not a URL.
 * Lenient on purpose: Claude may copy a fetched URL with small differences.
 */
export function normalizeUrl(value: unknown): string | null {
  const text = httpUrl(value)
  if (!text) return null
  const url = new URL(text)
  const host = url.hostname.replace(/^www\./, '')
  const path = url.pathname.length > 1 ? url.pathname.replace(/\/+$/, '') : ''
  return `${url.protocol}//${host}${url.port ? `:${url.port}` : ''}${path}${url.search}`
}

/** The host without "www.", for the per-domain log; '' if not a URL. */
export function domainOf(value: unknown): string {
  const text = httpUrl(value)
  return text ? new URL(text).hostname.replace(/^www\./, '') : ''
}

/**
 * Per fetched domain: pages fetched and candidates it gave (a posting counts
 * for its own url's domain, a listing candidate for its list page's domain).
 * For the log, e.g. "profession.hu 3 fetches -> 11 candidates (10 from lists)".
 */
export function domainStats(fetchedUrls: string[], input: unknown): string {
  const stats = new Map<string, { fetches: number; candidates: number; fromLists: number }>()
  const get = (domain: string) => {
    if (!stats.has(domain)) stats.set(domain, { fetches: 0, candidates: 0, fromLists: 0 })
    return stats.get(domain)!
  }
  for (const url of fetchedUrls) get(domainOf(url) || '(invalid)').fetches++
  const items = (input as { results?: unknown } | null)?.results
  for (const raw of Array.isArray(items) ? items : []) {
    const item = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>
    const fromList = item.source === 'listing'
    const entry = get(domainOf(fromList ? item.listing_url : item.url) || '(no url)')
    entry.candidates++
    if (fromList) entry.fromLists++
  }
  return [...stats]
    .sort((a, b) => b[1].candidates - a[1].candidates || b[1].fetches - a[1].fetches)
    .map(([domain, s]) => `${domain} ${s.fetches} fetches -> ${s.candidates} candidates` +
      (s.fromLists ? ` (${s.fromLists} from lists)` : ''))
    .join(', ') || '(nothing fetched)'
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

/** Which profile rule a job result breaks (counted separately per kind). */
export interface Dealbreaker {
  kind: 'senior' | 'years' | 'keyword'
  reason: string
}

/** Which profile rule a job result breaks, or null when it is fine. */
export function dealbreaker(
  item: { title: string; seniority: unknown; years_required: unknown },
  rules: ResultRules,
): Dealbreaker | null {
  const allowed = rules.allowedSeniority
  if (allowed.length > 0) {
    const level = typeof item.seniority === 'string' ? item.seniority.toLowerCase() : 'unknown'
    if (level !== 'unknown' && (SENIORITIES as readonly string[]).includes(level) && !allowed.includes(level)) {
      return { kind: 'senior', reason: `seniority ${level}` }
    }
    for (const [titleLevel, pattern] of Object.entries(TITLE_LEVEL_WORDS)) {
      if (!allowed.includes(titleLevel) && pattern.test(item.title)) {
        return { kind: 'senior', reason: `${titleLevel} in the title` }
      }
    }
  }
  const years = item.years_required
  if (rules.minYearsRequired !== null && typeof years === 'number' && years >= rules.minYearsRequired) {
    return { kind: 'years', reason: `requires ${years} years` }
  }
  const title = item.title.toLowerCase()
  const keyword = rules.titleKeywords.find((word) => title.includes(word))
  return keyword ? { kind: 'keyword', reason: `title contains "${keyword}"` } : null
}

// --- Claude's results --------------------------------------------------------------------

export const MAX_RESULTS = 30
/** Postings taken from one list page at most (keeps the model's answer short). */
export const MAX_PER_LISTING = 10

/** Why results were dropped, counted per reason (stored in search_runs, 010). */
export interface DroppedBy {
  invalid: number // no company/title, bad score, not an object
  duplicate: number
  over_limit: number // over MAX_RESULTS, or over MAX_PER_LISTING from one list page
  expired: number // jobs: the stated deadline is before today
  unconfirmed: number // jobs: posting not opened (or blocked domain); listing: list page not fetched
  bad_url: number // jobs: no URL, or a search/category/careers-index URL
  senior: number // jobs: a level the profile doesn't want (field or title word)
  years: number // jobs: requires min_years_required years or more
  dealbreaker: number // jobs: a dealbreaker title keyword
}

/** The search_runs columns (migration 010) for one run's counts. */
export function runCounts(candidates: number, droppedBy: DroppedBy) {
  return {
    candidates,
    dropped_unconfirmed: droppedBy.unconfirmed,
    dropped_senior: droppedBy.senior,
    dropped_years: droppedBy.years,
    dropped_dealbreaker: droppedBy.dealbreaker,
    dropped_bad_url: droppedBy.bad_url,
    dropped_other: droppedBy.invalid + droppedBy.duplicate + droppedBy.over_limit + droppedBy.expired,
  }
}

/** Shown in the reasons of a result taken from a list page (no extra column needed). */
export const LISTING_REASON = '~ seen on a job list page, the posting itself was not opened'

/**
 * Turn the submit_results tool input into rows we can store. Invalid items are
 * dropped (and counted), never "fixed up": a result without a company (or a
 * job without a title) is not trustworthy. Job results must also be confirmed,
 * link to one posting, pass the profile rules, and not have a deadline before
 * `today` (YYYY-MM-DD; null = don't check).
 *
 * Confirmed means one of two things (run 8: 7 fetches, mostly list pages, gave
 * 1 candidate, because only opened postings counted):
 *  - source "posting": Claude opened this exact url (confirmed: true)
 *  - source "listing": the posting was on a list page Claude fetched; its
 *    listing_url must be one of `fetchedUrls` (the pages really fetched in
 *    this run; null = don't check). Its url must still be the posting's own page.
 * Postings are processed before listings, so a posting seen both ways keeps the
 * opened version. Seniority/years rules only apply when known ("unknown", 0 pass).
 * Score and reasons come from score.ts with `scoring` (my profile + the run's
 * cities and roles), so the model only reports facts.
 */
export function validateResults(
  input: unknown,
  mode: Mode,
  rules: ResultRules = NO_RULES,
  today: string | null = null,
  fetchedUrls: string[] | null = null,
  scoring: ScoringContext = EMPTY_CONTEXT,
  maxPerListing = MAX_PER_LISTING, // smaller for Serbian city chunks (prompt.ts SERBIAN_BUDGET)
): { results: SearchResult[]; candidates: number; fromListings: number; dropped: number; droppedBy: DroppedBy } {
  const droppedBy: DroppedBy = {
    invalid: 0, duplicate: 0, over_limit: 0, expired: 0, unconfirmed: 0, bad_url: 0, senior: 0, years: 0,
    dealbreaker: 0,
  }
  const results: SearchResult[] = []
  let candidates = 0
  let fromListings = 0
  const done = () => {
    results.sort((a, b) => b.score - a.score)
    const dropped = Object.values(droppedBy).reduce((sum, n) => sum + n, 0)
    return { results, candidates, fromListings, dropped, droppedBy }
  }

  const items = (input as { results?: unknown } | null)?.results
  if (!Array.isArray(items)) return done()
  const seen = new Set<string>()
  const perListing = new Map<string, number>()
  const fetched = fetchedUrls === null ? null : new Set(fetchedUrls.map(normalizeUrl).filter((u) => u !== null))
  const isListing = (raw: unknown) => (raw as { source?: unknown } | null)?.source === 'listing'
  // Stable sort: postings first, each group in Claude's order.
  const considered = items.slice(0, MAX_RESULTS * 2)
    .sort((a, b) => Number(isListing(a)) - Number(isListing(b)))
  candidates = considered.length

  for (const raw of considered) {
    const item = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>
    const companyName = cleanText(item.company_name, 200)
    const title = mode === 'jobs' ? cleanText(item.title, 200) : ''
    const valid = companyName !== '' && (mode === 'companies' || title !== '')
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
    const listing = mode === 'jobs' && item.source === 'listing'
    if (mode === 'jobs') {
      const listingUrl = normalizeUrl(item.listing_url)
      const confirmed = listing
        ? listingUrl !== null && (fetched === null || fetched.has(listingUrl))
        : item.confirmed === true
      if (!confirmed || (url !== null && isFetchBlocked(url))) {
        droppedBy.unconfirmed++
        continue
      }
      // A listing candidate must point to its own posting, not back to the list.
      if (url === null || isListingUrl(url) || (listing && normalizeUrl(url) === listingUrl)) {
        droppedBy.bad_url++
        continue
      }
      const broken = dealbreaker({ title, seniority: item.seniority, years_required: item.years_required }, rules)
      if (broken) {
        droppedBy[broken.kind === 'keyword' ? 'dealbreaker' : broken.kind]++
        continue
      }
      const deadline = isoDate(item.deadline)
      if (today !== null && deadline !== null && deadline < today) { // YYYY-MM-DD compares as text
        droppedBy.expired++
        continue
      }
      if (listing) {
        const taken = perListing.get(listingUrl!) ?? 0
        if (taken >= maxPerListing) {
          droppedBy.over_limit++
          continue
        }
        perListing.set(listingUrl!, taken + 1)
      }
    }
    if (results.length >= MAX_RESULTS) {
      droppedBy.over_limit++
      continue
    }

    const city = cleanText(item.city, 100)
    const remote = typeof item.remote === 'boolean' ? item.remote : null
    const skills = stringList(item.skills, 8, 40) ?? []
    const careersContact = mode === 'companies' ? contact(item.contact) : null
    const { score, reasons } = mode === 'jobs'
      ? scoreJob({ title, city, remote: remote === true, seniority: cleanText(item.seniority, 10).toLowerCase(), skills }, scoring)
      : scoreCompany({ city, remote: remote === true, skills, juniorFriendly: item.junior_friendly === true,
        hasContact: careersContact !== null }, scoring)
    if (listing) fromListings++
    results.push({
      kind: mode === 'jobs' ? 'job' : 'company',
      title: title || null,
      company_name: companyName,
      city: city || null,
      remote,
      url,
      deadline: mode === 'jobs' ? isoDate(item.deadline) : null,
      contact: careersContact,
      reasons: (listing ? [LISTING_REASON, ...reasons] : reasons).slice(0, 8),
      score,
    })
  }
  return done()
}
