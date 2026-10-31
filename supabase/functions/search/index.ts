// Edge Function "search" (Phase 9.3): AI search for job postings or IT companies.
//
// Browser (logged in) --POST {mode, params}--> this function --> Claude + web search
//                                                   |--> search_runs / search_results
//
// Why an Edge Function and not the browser: the ANTHROPIC_API_KEY must never
// reach the browser. It is a Supabase secret, readable only here, server-side.
// Why not the Python pipeline: the search is started by a click, not daily.
//
// A search run is split into chunks (run 9: one long request per run timed
// out): one per city, plus remote. The dashboard calls this function once to
// start the run, then once per chunk, one after another:
//   {action: "start", mode, params} -> rate limit (start_search_run, max 1 run
//        per hour -> 429), the chunk list -> {run_id, chunks: ["Szeged", ...]}
//   {action: "chunk", run_id, chunk} -> claim_search_chunk() (each chunk runs
//        once, in order), one Claude conversation (claude-haiku-5-5, web search
//        + web fetch, small budget, 60 s), the answer as ONE submit_results
//        call (strict JSON, facts only), validated, scored by our code
//        (score.ts), stored; the run's counts add up chunk by chunk
// Every call first checks the caller is a logged-in user (no token -> 401).
// The function itself never acts on web content: the searches and page fetches
// run on Anthropic's side, our code fetches nothing and writes only its own two
// tables. Web pages can only end up here as validated JSON.

import Anthropic from 'npm:@anthropic-ai/sdk@1.3.0'
import { createClient } from 'npm:@supabase/supabase-js@2.118.0'
import {
  FETCH_FIRST_MESSAGE, KEEP_GOING_MESSAGE, MAX_FETCHES, MAX_SEARCHES, MIN_CANDIDATES, MODEL, type Profile,
  SUBMIT_RESULTS_TOOL, SYSTEM_PROMPT, buildChunks, buildUserPrompt, webFetchTool, webSearchTool,
} from './prompt.ts'
import { scoringContext } from './score.ts'
import {
  type Mode, type SearchParams, domainStats, rulesFromProfile, runCounts, validateAction, validateRequest,
  validateResults,
} from './validate.ts'

// Supabase kills a function after 150 s (wall clock). Run 4 was killed (a
// timeout retried by the SDK); run 9 timed out in ONE long request, which the
// wrap-up below can't interrupt. Now: no retries, and each CHUNK (its own
// function call, a small budget) finishes within 60 s.
const TIME_BUDGET_MS = 60_000 // one chunk, Claude requests included
const WRAP_UP_AFTER_MS = 35_000 // after this: "stop searching, submit what you confirmed"
const WRAP_UP_RESERVE_MS = 15_000 // kept free for that last, short request
const MAX_REQUESTS = 6 // Claude API requests per chunk (pause_turn continuations, the two guards, wrap-up)

// The message that ends a run early. Claude keeps everything it already found
// (it is in the conversation), so the confirmed postings are saved, not lost.
const WRAP_UP_MESSAGE =
  'Time is up. Do not search or fetch anything else. Call submit_results now with the postings ' +
  'you have already opened and the postings on list pages you already fetched (an empty list if none).'

// The page that calls us is on another origin (localhost / Vercel). '*' is
// fine here: access is controlled by the Authorization header (a user token),
// not by cookies, so another site can't use my login.
const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
}

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json', ...headers },
  })
}

// New-style keys (SUPABASE_SECRET_KEYS, a JSON map) when the project has
// them, else the legacy ones. Read here, never logged, never returned.
function supabaseKey(jsonName: string, legacyName: string): string {
  try {
    const key = JSON.parse(Deno.env.get(jsonName) ?? '{}').default
    if (typeof key === 'string' && key) return key
  } catch {
    // not set or not JSON: fall back to the legacy key
  }
  const legacy = Deno.env.get(legacyName)
  if (!legacy) throw new Error(`${legacyName} is not set`)
  return legacy
}

/** Today in my time zone, YYYY-MM-DD (sv-SE formats dates like that). */
function budapestToday(): string {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Budapest' }).format(new Date())
}

interface Usage {
  searches: number
  fetches: number // fetches cost tokens (counted below), no fee
  fetchCalls: number // web_fetch calls seen in the responses, failed ones included
  fetchErrors: string[] // their error codes, e.g. "url_not_accessible"
  fetchedUrls: string[] // pages fetched successfully: a "listing" result must come from one of them
  fetchCallUrls: string[] // the URL of every web_fetch call (failed ones too), for the per-domain log
  input: number
  output: number
}

function addUsage(total: Usage, usage: Anthropic.Messages.Usage): void {
  total.searches += usage.server_tool_use?.web_search_requests ?? 0
  total.fetches += usage.server_tool_use?.web_fetch_requests ?? 0
  // Cache reads/writes are input tokens too (billed at other rates).
  total.input += usage.input_tokens + (usage.cache_creation_input_tokens ?? 0) + (usage.cache_read_input_tokens ?? 0)
  total.output += usage.output_tokens
}

/** Pages opened (or tried): failed fetches count against max_uses too. */
function fetchesUsed(usage: Usage): number {
  return Math.max(usage.fetches, usage.fetchCalls)
}

/**
 * Look at one response's blocks (direct tool calls come back in full): count
 * web_fetch calls, their URLs and error codes, and return a one-line summary for
 * the log, e.g. "pause_turn: server_tool_use:web_search x3, web_search_tool_result x3".
 * Only block types, tool names, URLs and error codes: never page content.
 */
function inspectResponse(response: Anthropic.Messages.Message, usage: Usage): string {
  const counts = new Map<string, number>()
  const requested = new Map<string, string>() // web_fetch tool_use id -> requested URL
  for (const block of response.content as unknown as Array<Record<string, unknown>>) {
    const label = block.type === 'server_tool_use' || block.type === 'tool_use' ? `${block.type}:${block.name}` : String(block.type)
    counts.set(label, (counts.get(label) ?? 0) + 1)
    if (block.type === 'server_tool_use' && block.name === 'web_fetch') {
      usage.fetchCalls++
      const url = String((block.input as { url?: unknown } | undefined)?.url ?? '')
      usage.fetchCallUrls.push(url)
      requested.set(String(block.id), url)
    }
    const content = block.content as { type?: string; error_code?: string; url?: unknown } | undefined
    if (block.type === 'web_fetch_tool_result' && content?.type === 'web_fetch_tool_result_error') {
      usage.fetchErrors.push(content.error_code ?? 'unknown')
    } else if (block.type === 'web_fetch_tool_result') {
      // A successful fetch: both the URL Claude asked for and the one the
      // fetcher read (after redirects) count as "this page was fetched".
      const asked = requested.get(String(block.tool_use_id))
      if (asked) usage.fetchedUrls.push(asked)
      if (typeof content?.url === 'string') usage.fetchedUrls.push(content.url)
    }
  }
  const parts = [...counts].map(([label, n]) => (n > 1 ? `${label} x${n}` : label))
  return `${response.stop_reason}: ${parts.join(', ')}`
}

/**
 * Ask Claude. Returns the raw submit_results input (validated by the caller)
 * and whether the run was cut short by the time budget. Adds every response's
 * usage to `usage`, also when it throws, so a failed run still records its cost.
 *
 * Time: normal requests may use the budget minus WRAP_UP_RESERVE_MS. After
 * WRAP_UP_AFTER_MS (or when a request times out after earlier rounds), one
 * last request asks Claude to submit what it already confirmed; that request
 * gets the reserve. So the run ends within TIME_BUDGET_MS either way.
 */
async function askClaude(
  client: Anthropic,
  mode: Mode,
  params: SearchParams,
  profile: Profile | null,
  appliedCompanies: string[],
  usage: Usage,
  startedAt: number,
  tag: string, // "run 12 / Szeged", for the log
): Promise<{ input: unknown; stoppedEarly: boolean }> {
  const messages: Anthropic.Messages.MessageParam[] = [
    { role: 'user', content: buildUserPrompt(mode, params, profile, appliedCompanies, budapestToday()) },
  ]
  let reminded = false
  let wrappingUp = false
  let fetchFirstSent = false // the "open postings before submitting" rejection is sent once
  let keepGoingSent = false // the "fewer than MIN_CANDIDATES, keep going" rejection is sent once
  const elapsed = () => Date.now() - startedAt
  const wrapUp = () => {
    wrappingUp = true
    messages.push({ role: 'user', content: WRAP_UP_MESSAGE })
  }

  for (let request = 0; request < MAX_REQUESTS; request++) {
    const timeout = TIME_BUDGET_MS - elapsed() - (wrappingUp ? 0 : WRAP_UP_RESERVE_MS)
    if (timeout < 5_000) throw new Error('Time limit reached before Claude finished')
    // max_uses is per API request: give each continuation only what is left
    // of the budget, so a run never goes over it.
    const searchesLeft = MAX_SEARCHES - usage.searches
    const fetchesLeft = MAX_FETCHES - fetchesUsed(usage)
    if (searchesLeft <= 0 && reminded) throw new Error('Search budget used up without results')

    let response: Anthropic.Messages.Message
    try {
      response = await client.messages.create(
        {
          model: MODEL,
          max_tokens: 16000,
          // medium effort: at low, run 6 ran 3 of 8 searches and 2 of 8 fetches and
          // gave up after 15 s. Medium works through the plan; the time budget
          // still ends every chunk within 60 s. Thinking stays adaptive (default).
          output_config: { effort: 'medium' },
          system: SYSTEM_PROMPT,
          tools: [webSearchTool(Math.max(searchesLeft, 1)), webFetchTool(Math.max(fetchesLeft, 1)), SUBMIT_RESULTS_TOOL],
          messages,
        },
        // maxRetries 0: a retried timeout would double the time (run 4).
        { timeout, maxRetries: 0 },
      )
    } catch (error) {
      // A normal request ran out of time. If earlier rounds already found
      // something (the conversation has more than the first message), ask
      // for it with the reserved time instead of losing everything.
      if (error instanceof Anthropic.APIConnectionTimeoutError && !wrappingUp && messages.length > 1) {
        if (messages[messages.length - 1].role === 'assistant') wrapUp()
        else wrappingUp = true // ends with a user message already: just retry it with the reserve
        continue
      }
      if (error instanceof Anthropic.APIConnectionTimeoutError) throw new Error('Time limit reached before Claude finished')
      throw error
    }
    addUsage(usage, response.usage)
    console.log(`search ${tag} request ${request + 1} (${Math.round(elapsed() / 1000)} s): ${inspectResponse(response, usage)}`)

    const submit = response.content.find(
      (block): block is Anthropic.Messages.ToolUseBlock => block.type === 'tool_use' && block.name === 'submit_results',
    )
    // Two guards against giving up early, each used once and only while there
    // is time (before the wrap-up). submit_results is our tool, so we reject a
    // call by answering it with an error tool_result; Claude then continues.
    //  - no posting opened yet (run 5): nothing could be confirmed
    //  - fewer than MIN_CANDIDATES postings while searches are left (run 6; per chunk now)
    const reject = (message: string) => {
      messages.push({ role: 'assistant', content: response.content })
      messages.push({
        role: 'user',
        content: [{ type: 'tool_result', tool_use_id: submit!.id, content: message, is_error: true }],
      })
    }
    const canContinue = !wrappingUp && elapsed() < WRAP_UP_AFTER_MS
    if (submit && mode === 'jobs' && canContinue) {
      const submitted = (submit.input as { results?: unknown })?.results
      const candidates = Array.isArray(submitted) ? submitted.length : 0
      if (fetchesUsed(usage) === 0 && !fetchFirstSent) {
        fetchFirstSent = true
        reject(FETCH_FIRST_MESSAGE)
        continue
      }
      if (candidates < MIN_CANDIDATES && MAX_SEARCHES - usage.searches > 0 && !keepGoingSent) {
        keepGoingSent = true
        console.log(`search ${tag}: ${candidates} candidates, ${usage.searches} searches: keep going`)
        reject(KEEP_GOING_MESSAGE)
        continue
      }
    }
    if (submit) return { input: submit.input, stoppedEarly: wrappingUp }

    switch (response.stop_reason) {
      case 'pause_turn':
        // The server-side search loop paused: send the turn back. Normally it
        // continues where it stopped; near the time budget, ask it to wrap up.
        // (If it pauses during the wrap-up, repeat the request; the timeout
        // check at the top of the loop still ends the run in time.)
        messages.push({ role: 'assistant', content: response.content })
        if (wrappingUp || elapsed() >= WRAP_UP_AFTER_MS) wrapUp()
        continue
      case 'end_turn':
        // Answered in text instead of calling the tool: remind it once.
        if (reminded || wrappingUp) throw new Error('Claude did not return results')
        reminded = true
        messages.push({ role: 'assistant', content: response.content })
        messages.push({
          role: 'user',
          content: 'Call submit_results now with the results you found (an empty list if none).',
        })
        continue
      case 'refusal':
        throw new Error('Claude declined the request')
      case 'max_tokens':
        throw new Error('The answer was too long (max_tokens)')
      default:
        throw new Error(`Unexpected stop reason: ${response.stop_reason}`)
    }
  }
  throw new Error('Too many API requests without results')
}

// Anything that looks like an Anthropic key is cut out of error texts, just in
// case an error message ever echoes one. The key itself is never passed here.
const KEY_PATTERN = /sk-ant-[A-Za-z0-9_-]+/g

/**
 * A short, safe error text for the database and the browser. For API errors:
 * status + the error type and message from the response BODY (e.g.
 * "invalid_request_error: tools.0.web_search_20260318: Country code HU is not
 * supported.") + the request id, so
 * the cause is visible in search_runs.error. Never the headers or the request:
 * the body's message is all we need, and it doesn't contain the key.
 */
function describeError(error: unknown): string {
  if (error instanceof Anthropic.APIConnectionTimeoutError) return 'Claude API: timed out'
  if (error instanceof Anthropic.AuthenticationError) return 'Claude API: the API key is invalid or missing (401)'
  if (error instanceof Anthropic.APIError) {
    // Body shape: {type: "error", error: {type, message}, request_id}
    const body = (error.error ?? {}) as { error?: { type?: unknown; message?: unknown } }
    const type = typeof body.error?.type === 'string' ? body.error.type : 'api_error'
    const message = typeof body.error?.message === 'string' ? body.error.message : error.message
    const requestId = error.requestID ? ` [request ${error.requestID}]` : ''
    return `Claude API ${error.status ?? ''} ${type}: ${message}${requestId}`.replace(KEY_PATTERN, '[redacted]').slice(0, 600)
  }
  if (error instanceof Error) return error.message.replace(KEY_PATTERN, '[redacted]').slice(0, 300)
  return 'Unknown error'
}

type Admin = ReturnType<typeof createClient>

/** {action: "start"}: the rate limit, then the chunk list for the dashboard. */
async function startRun(admin: Admin, mode: Mode, params: SearchParams): Promise<Response> {
  const chunks = buildChunks(params)
  const { data: started, error: startError } = await admin.rpc('start_search_run', { p_mode: mode, p_params: params })
  if (startError) return json(500, { error: 'Could not start the search run' })
  const { run_id: runId, next_allowed_at: nextAllowedAt } = started[0]
  if (runId === null) {
    const retryAfter = Math.max(1, Math.ceil((new Date(nextAllowedAt).getTime() - Date.now()) / 1000))
    return json(429, { error: 'One search per hour', next_allowed_at: nextAllowedAt }, { 'Retry-After': String(retryAfter) })
  }
  const { error } = await admin.from('search_runs').update({ chunks_total: chunks.length }).eq('id', runId)
  if (error) {
    // 013 not applied: close the run (status error never counts) instead of leaving it running.
    await admin.from('search_runs')
      .update({ status: 'error', error: 'chunks not supported: is migration 013 applied?', finished_at: new Date().toISOString() })
      .eq('id', runId)
    return json(500, { error: 'The database is not ready for chunked searches (migration 013)' })
  }
  console.log(`search run ${runId} (${mode}): started, chunks ${chunks.map((c) => c.label).join(', ')}`)
  return json(200, { run_id: runId, chunks: chunks.map((chunk) => chunk.label) })
}

/**
 * Add one chunk's usage and counts to its run (chunks run one after another,
 * enforced by claim_search_chunk, so read-then-write is safe). The last chunk
 * closes the run: success if at least one chunk worked, else error.
 */
async function recordChunk(
  admin: Admin,
  runId: number,
  index: number,
  total: number,
  usage: Usage,
  counts: Record<string, number | boolean> | null, // null = the chunk failed
  failure: string | null,
): Promise<void> {
  const { data: run } = await admin.from('search_runs').select('*').eq('id', runId).single()
  if (!run) return
  const add = (key: string) => (run[key] ?? 0) + Number(counts?.[key] ?? 0)
  const failed = run.chunks_failed + (counts ? 0 : 1)
  const last = index === total - 1
  const fields: Record<string, unknown> = {
    searches_used: run.searches_used + usage.searches,
    input_tokens: run.input_tokens + usage.input,
    output_tokens: run.output_tokens + usage.output,
    fetches_used: run.fetches_used + fetchesUsed(usage),
    candidates: add('candidates'),
    dropped_unconfirmed: add('dropped_unconfirmed'),
    dropped_senior: add('dropped_senior'),
    dropped_years: add('dropped_years'),
    dropped_dealbreaker: add('dropped_dealbreaker'),
    dropped_bad_url: add('dropped_bad_url'),
    dropped_other: add('dropped_other'),
    stopped_early: run.stopped_early || counts?.stopped_early === true,
    chunks_failed: failed,
    last_activity_at: new Date().toISOString(),
    error: failure ? [run.error, failure].filter(Boolean).join('; ').slice(0, 1000) : run.error,
  }
  if (last) {
    fields.status = failed < total ? 'success' : 'error'
    fields.finished_at = new Date().toISOString()
  }
  const { error } = await admin.from('search_runs').update(fields).eq('id', runId)
  if (error) console.error(`search run ${runId}: could not record chunk ${index} (${error.message})`)
}

/** {action: "chunk"}: one chunk of a running run. */
async function runChunk(admin: Admin, anthropicKey: string, runId: number, index: number): Promise<Response> {
  const { data: claimed, error: claimError } = await admin.rpc('claim_search_chunk', { p_run_id: runId, p_chunk: index })
  if (claimError) return json(500, { error: 'Could not start this part of the search' })
  if (!claimed?.length) return json(409, { error: 'This part of the search already ran, or the search is finished' })
  // The params were validated when the run started; check again (they come back as JSON).
  const stored = validateRequest({ mode: claimed[0].mode, params: claimed[0].params })
  const total: number = claimed[0].chunks_total
  const chunk = stored.ok ? buildChunks(stored.value.params)[index] : undefined
  const usage: Usage = {
    searches: 0, fetches: 0, fetchCalls: 0, fetchErrors: [], fetchedUrls: [], fetchCallUrls: [], input: 0, output: 0,
  }
  if (!stored.ok || !chunk) {
    await recordChunk(admin, runId, index, total, usage, null, `chunk ${index}: invalid stored parameters`)
    return json(500, { error: 'Invalid stored search parameters' })
  }
  const { mode, params } = stored.value
  const tag = `run ${runId} / ${chunk.label}`
  const startedAt = Date.now()

  try {
    const [{ data: profile }, { data: applied }] = await Promise.all([
      admin.from('search_profile').select('skills, seniority, cities, languages, preferred_roles, dealbreakers').maybeSingle(),
      admin.from('applications').select('company_name').limit(500),
    ])
    const appliedCompanies = [...new Set((applied ?? []).map((row: { company_name: string }) => row.company_name))]

    const client = new Anthropic({ apiKey: anthropicKey })
    const { input: raw, stoppedEarly } =
      await askClaude(client, mode, chunk.params, profile as Profile | null, appliedCompanies, usage, startedAt, tag)

    // Validate (incl. the dealbreakers), score with ALL the run's cities and
    // roles (so a chunk's city keeps its priority), store.
    // already_applied is set by a database trigger.
    const { results, candidates, fromListings, dropped, droppedBy } = validateResults(
      raw, mode, rulesFromProfile(profile), budapestToday(), usage.fetchedUrls, scoringContext(params, profile),
    )
    const counts = { ...runCounts(candidates, droppedBy), stopped_early: stoppedEarly }
    let saved: unknown[] = []
    if (results.length > 0) {
      const { data, error } = await admin
        .from('search_results')
        .insert(results.map((result) => ({ ...result, run_id: runId })))
        .select('id, kind, title, company_name, city, remote, url, deadline, contact, reasons, score, already_applied, company_key')
      if (error) throw new Error(`Could not store the results: ${error.message}`)
      saved = data ?? []
    }
    await recordChunk(admin, runId, index, total, usage, counts, null)
    console.log(`search ${tag} domains: ${domainStats(usage.fetchCallUrls, raw)}`)
    console.log(`search ${tag} (${mode}): ${saved.length} results (${fromListings} from lists) of ${candidates} candidates, ` +
      `${dropped} dropped ${JSON.stringify(droppedBy)}, ${usage.searches} searches, ` +
      `${fetchesUsed(usage)} fetches (errors: ${usage.fetchErrors.join(', ') || 'none'}), ` +
      `${usage.input} in / ${usage.output} out tokens, ${Math.round((Date.now() - startedAt) / 1000)} s` +
      (stoppedEarly ? ', stopped early (time budget)' : ''))

    return json(200, {
      run_id: runId,
      chunk: index,
      label: chunk.label,
      results: saved,
      stopped_early: stoppedEarly,
      counts: { ...counts, fetches_used: fetchesUsed(usage) },
    })
  } catch (error) {
    const message = describeError(error)
    console.error(`search ${tag} failed: ${message}`)
    await recordChunk(admin, runId, index, total, usage, null, `${chunk.label}: ${message}`)
    return json(502, { run_id: runId, chunk: index, label: chunk.label, error: message })
  }
}

Deno.serve(async (req: Request) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS_HEADERS }) // CORS preflight
  if (req.method !== 'POST') return json(405, { error: 'Use POST' })

  const supabaseUrl = Deno.env.get('SUPABASE_URL')!
  const anthropicKey = Deno.env.get('ANTHROPIC_API_KEY')
  if (!anthropicKey) return json(500, { error: 'ANTHROPIC_API_KEY is not set (Edge Function secret)' })

  // 1. Who is calling? getUser() asks Supabase Auth to verify the token.
  // The public anon key is also a valid JWT, but not a user: getUser() fails.
  const token = (req.headers.get('Authorization') ?? '').replace(/^Bearer\s+/i, '')
  const authClient = createClient(supabaseUrl, supabaseKey('SUPABASE_PUBLISHABLE_KEYS', 'SUPABASE_ANON_KEY'), {
    auth: { persistSession: false, autoRefreshToken: false },
  })
  const { data: userData, error: userError } = await authClient.auth.getUser(token)
  if (userError || !userData.user) return json(401, { error: 'Log in first' })

  // 2. Validate the body: start a run, or run one of its chunks.
  let body: unknown
  try {
    body = await req.json()
  } catch {
    return json(400, { error: 'The body must be JSON' })
  }
  const request = validateAction(body)
  if (!request.ok) return json(400, { error: request.error })

  // The service_role/secret client bypasses RLS: it is the only writer of
  // search_runs and search_results. It exists only inside this function.
  const admin = createClient(supabaseUrl, supabaseKey('SUPABASE_SECRET_KEYS', 'SUPABASE_SERVICE_ROLE_KEY'), {
    auth: { persistSession: false, autoRefreshToken: false },
  })

  const action = request.value
  return action.action === 'start'
    ? startRun(admin, action.mode, action.params)
    : runChunk(admin, anthropicKey, action.runId, action.chunk)
})
