// Edge Function "search" (Phase 9.3): AI search for job postings or IT companies.
//
// Browser (logged in) --POST {mode, params}--> this function --> Claude + web search
//                                                   |--> search_runs / search_results
//
// Why an Edge Function and not the browser: the ANTHROPIC_API_KEY must never
// reach the browser. It is a Supabase secret, readable only here, server-side.
// Why not the Python pipeline: the search is started by a click, not daily.
//
// What it does, in order:
//   1. checks the caller is a logged-in user (anon / no token -> 401)
//   2. validates the request body (validate.ts)
//   3. rate limit: start_search_run() in the database (max 1 per hour -> 429)
//   4. asks Claude (claude-haiku-5-5) with web search + web fetch (Claude opens
//      candidate postings to confirm them); the answer comes back as ONE call
//      of the submit_results tool (strict JSON)
//   5. validates every result field and the profile's dealbreakers, stores the
//      results and the cost
// The function itself never acts on web content: the searches and page fetches
// run on Anthropic's side, our code fetches nothing and writes only its own two
// tables. Web pages can only end up here as validated JSON.

import Anthropic from 'npm:@anthropic-ai/sdk@1.3.0'
import { createClient } from 'npm:@supabase/supabase-js@2.118.0'
import {
  FETCH_FIRST_MESSAGE, MAX_FETCHES, MAX_SEARCHES, MODEL, type Profile, SUBMIT_RESULTS_TOOL, SYSTEM_PROMPT,
  buildUserPrompt, webFetchTool, webSearchTool,
} from './prompt.ts'
import { type Mode, type SearchParams, rulesFromProfile, runCounts, validateRequest, validateResults } from './validate.ts'

// Supabase kills a function after 150 s (wall clock). Run 4 was killed: its
// last request had timeout = time left AND one SDK retry, so it could take
// twice as long. Now: no retries, and every run finishes within ~95 s.
const TIME_BUDGET_MS = 95_000 // the whole run, Claude requests included
const WRAP_UP_AFTER_MS = 55_000 // after this: "stop searching, submit what you confirmed"
const WRAP_UP_RESERVE_MS = 25_000 // kept free for that last, short request
const MAX_REQUESTS = 6 // Claude API requests per run (pause_turn continuations + wrap-up)

// The message that ends a run early. Claude keeps everything it already found
// (it is in the conversation), so the confirmed postings are saved, not lost.
const WRAP_UP_MESSAGE =
  'Time is up. Do not search or fetch anything else. Call submit_results now with only the ' +
  'postings you have already opened and confirmed (an empty list if none).'

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
 * web_fetch calls and their error codes, and return a one-line summary for the
 * log, e.g. "pause_turn: server_tool_use:web_search x3, web_search_tool_result x3".
 * Only block types, tool names and error codes: never page content.
 */
function inspectResponse(response: Anthropic.Messages.Message, usage: Usage): string {
  const counts = new Map<string, number>()
  for (const block of response.content as unknown as Array<Record<string, unknown>>) {
    const label = block.type === 'server_tool_use' || block.type === 'tool_use' ? `${block.type}:${block.name}` : String(block.type)
    counts.set(label, (counts.get(label) ?? 0) + 1)
    if (block.type === 'server_tool_use' && block.name === 'web_fetch') usage.fetchCalls++
    const content = block.content as { type?: string; error_code?: string } | undefined
    if (block.type === 'web_fetch_tool_result' && content?.type === 'web_fetch_tool_result_error') {
      usage.fetchErrors.push(content.error_code ?? 'unknown')
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
  runId: number,
): Promise<{ input: unknown; stoppedEarly: boolean }> {
  const messages: Anthropic.Messages.MessageParam[] = [
    { role: 'user', content: buildUserPrompt(mode, params, profile, appliedCompanies, budapestToday()) },
  ]
  let reminded = false
  let wrappingUp = false
  let fetchFirstSent = false // the "open postings before submitting" rejection is sent once
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
          // low effort: fewer, more consolidated tool calls -> faster and cheaper,
          // which matters with the time budget. Thinking stays adaptive (default).
          output_config: { effort: 'low' },
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
    console.log(`search run ${runId} request ${request + 1} (${Math.round(elapsed() / 1000)} s): ${inspectResponse(response, usage)}`)

    const submit = response.content.find(
      (block): block is Anthropic.Messages.ToolUseBlock => block.type === 'tool_use' && block.name === 'submit_results',
    )
    // Submitting job results without having opened a single posting (run 5)
    // means nothing can be confirmed. While there is time and fetch budget,
    // reject that once: submit_results is our tool, so we answer its call with
    // an error tool_result and Claude continues (opens postings, submits again).
    if (submit && mode === 'jobs' && fetchesUsed(usage) === 0 && !fetchFirstSent && !wrappingUp &&
      elapsed() < WRAP_UP_AFTER_MS) {
      fetchFirstSent = true
      messages.push({ role: 'assistant', content: response.content })
      messages.push({
        role: 'user',
        content: [{ type: 'tool_result', tool_use_id: submit.id, content: FETCH_FIRST_MESSAGE, is_error: true }],
      })
      continue
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

  // 2. Validate the body.
  let body: unknown
  try {
    body = await req.json()
  } catch {
    return json(400, { error: 'The body must be JSON' })
  }
  const request = validateRequest(body)
  if (!request.ok) return json(400, { error: request.error })
  const { mode, params } = request.value

  // The service_role/secret client bypasses RLS: it is the only writer of
  // search_runs and search_results. It exists only inside this function.
  const admin = createClient(supabaseUrl, supabaseKey('SUPABASE_SECRET_KEYS', 'SUPABASE_SERVICE_ROLE_KEY'), {
    auth: { persistSession: false, autoRefreshToken: false },
  })

  // 3. Rate limit (and create the run) in one database call.
  const { data: started, error: startError } = await admin.rpc('start_search_run', { p_mode: mode, p_params: params })
  if (startError) return json(500, { error: 'Could not start the search run' })
  const { run_id: runId, next_allowed_at: nextAllowedAt } = started[0]
  if (runId === null) {
    const retryAfter = Math.max(1, Math.ceil((new Date(nextAllowedAt).getTime() - Date.now()) / 1000))
    return json(429, { error: 'One search per hour', next_allowed_at: nextAllowedAt }, { 'Retry-After': String(retryAfter) })
  }

  const startedAt = Date.now()
  const usage: Usage = { searches: 0, fetches: 0, fetchCalls: 0, fetchErrors: [], input: 0, output: 0 }
  // Close the run. `counts` are the 010 columns (fetches, candidates, drops).
  // If the database doesn't have them yet (migration 010 not applied), save
  // the run without them: a run must never stay 'running' because of that.
  const finish = async (fields: Record<string, unknown>, counts: Record<string, unknown> = {}) => {
    const base = {
      finished_at: new Date().toISOString(),
      searches_used: usage.searches,
      input_tokens: usage.input,
      output_tokens: usage.output,
      ...fields,
    }
    const { error } = await admin
      .from('search_runs')
      .update({ ...base, fetches_used: fetchesUsed(usage), ...counts })
      .eq('id', runId)
    if (error) {
      console.warn(`search run ${runId}: counts not saved (${error.message}); is migration 010 applied?`)
      await admin.from('search_runs').update(base).eq('id', runId)
    }
  }

  try {
    const [{ data: profile }, { data: applied }] = await Promise.all([
      admin.from('search_profile').select('skills, seniority, cities, languages, preferred_roles, dealbreakers').maybeSingle(),
      admin.from('applications').select('company_name').limit(500),
    ])
    const appliedCompanies = [...new Set((applied ?? []).map((row: { company_name: string }) => row.company_name))]

    // 4. Claude + web search.
    const client = new Anthropic({ apiKey: anthropicKey })
    const { input: raw, stoppedEarly } =
      await askClaude(client, mode, params, profile as Profile | null, appliedCompanies, usage, startedAt, runId)

    // 5. Validate (incl. the profile's dealbreakers) and store.
    // already_applied is set by a database trigger.
    const { results, candidates, dropped, droppedBy } = validateResults(raw, mode, rulesFromProfile(profile))
    const counts = { ...runCounts(candidates, droppedBy), stopped_early: stoppedEarly }
    let stored: unknown[] = []
    if (results.length > 0) {
      const { data, error } = await admin
        .from('search_results')
        .insert(results.map((result) => ({ ...result, run_id: runId })))
        .select('id, kind, title, company_name, city, remote, url, deadline, contact, reasons, score, already_applied')
      if (error) throw new Error(`Could not store the results: ${error.message}`)
      stored = (data ?? []).sort((a: { score: number }, b: { score: number }) => b.score - a.score)
    }
    await finish({ status: 'success' }, counts)
    console.log(`search run ${runId} (${mode}): ${stored.length} results of ${candidates} candidates, ` +
      `${dropped} dropped ${JSON.stringify(droppedBy)}, ${usage.searches} searches, ` +
      `${fetchesUsed(usage)} fetches (errors: ${usage.fetchErrors.join(', ') || 'none'}), ` +
      `${usage.input} in / ${usage.output} out tokens, ${Math.round((Date.now() - startedAt) / 1000)} s` +
      (stoppedEarly ? ', stopped early (time budget)' : ''))

    return json(200, {
      run_id: runId,
      results: stored,
      dropped,
      dropped_by: droppedBy,
      stopped_early: stoppedEarly, // the page tells me the list may be incomplete
      // The same counts as in search_runs (010), so the page can explain a run
      // with 0 results even before the migration is applied.
      counts: { ...counts, fetches_used: fetchesUsed(usage) },
      usage: { searches: usage.searches, input_tokens: usage.input, output_tokens: usage.output },
    })
  } catch (error) {
    const message = describeError(error)
    console.error(`search run ${runId} failed: ${message}`)
    await finish({ status: 'error', error: message })
    return json(502, { run_id: runId, error: message })
  }
})
