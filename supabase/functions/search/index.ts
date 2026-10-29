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
//   4. asks Claude (claude-haiku-5-5) with the web search tool; the answer
//      comes back as ONE call of the submit_results tool (strict JSON)
//   5. validates every result field, stores the results and the cost
// The function never acts on web content: no fetches, no other tools, no
// writes besides its own two tables. Web pages can only end up as validated JSON.

import Anthropic from 'npm:@anthropic-ai/sdk@1.3.0'
import { createClient } from 'npm:@supabase/supabase-js@2.118.0'
import { MAX_SEARCHES, MODEL, type Profile, SUBMIT_RESULTS_TOOL, SYSTEM_PROMPT, buildUserPrompt, webSearchTool } from './prompt.ts'
import { type Mode, type SearchParams, validateRequest, validateResults } from './validate.ts'

// Supabase stops a function after 150 s (free plan). Stop on our own before
// that, so the run can still be marked as failed and its cost recorded.
const DEADLINE_MS = 130_000
const MAX_REQUESTS = 6 // Claude API requests per run (pause_turn continuations + one reminder)

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
  input: number
  output: number
}

function addUsage(total: Usage, usage: Anthropic.Messages.Usage): void {
  total.searches += usage.server_tool_use?.web_search_requests ?? 0
  // Cache reads/writes are input tokens too (billed at other rates).
  total.input += usage.input_tokens + (usage.cache_creation_input_tokens ?? 0) + (usage.cache_read_input_tokens ?? 0)
  total.output += usage.output_tokens
}

/**
 * Ask Claude. Returns the raw submit_results input (validated by the caller).
 * Adds every response's usage to `usage`, also when it throws, so a failed
 * run still records what it cost.
 */
async function askClaude(
  client: Anthropic,
  mode: Mode,
  params: SearchParams,
  profile: Profile | null,
  appliedCompanies: string[],
  usage: Usage,
  startedAt: number,
): Promise<unknown> {
  const messages: Anthropic.Messages.MessageParam[] = [
    { role: 'user', content: buildUserPrompt(mode, params, profile, appliedCompanies, budapestToday()) },
  ]
  let reminded = false

  for (let request = 0; request < MAX_REQUESTS; request++) {
    const timeLeft = DEADLINE_MS - (Date.now() - startedAt)
    if (timeLeft < 10_000) throw new Error('Time limit reached before Claude finished')
    // max_uses is per API request: give each continuation only what is left
    // of the 15, so a run never pays for more than MAX_SEARCHES searches.
    const searchesLeft = MAX_SEARCHES - usage.searches
    if (searchesLeft <= 0 && reminded) throw new Error('Search budget used up without results')

    const response = await client.messages.create(
      {
        model: MODEL,
        max_tokens: 16000,
        // low effort: fewer, more consolidated tool calls -> faster and cheaper,
        // which matters with the 150 s limit. Thinking stays adaptive (default).
        output_config: { effort: 'low' },
        system: SYSTEM_PROMPT,
        tools: [webSearchTool(params, Math.max(searchesLeft, 1)), SUBMIT_RESULTS_TOOL],
        messages,
      },
      { timeout: timeLeft, maxRetries: 1 },
    )
    addUsage(usage, response.usage)

    const submit = response.content.find(
      (block): block is Anthropic.Messages.ToolUseBlock => block.type === 'tool_use' && block.name === 'submit_results',
    )
    if (submit) return submit.input

    switch (response.stop_reason) {
      case 'pause_turn':
        // The server-side search loop paused: send the turn back unchanged and
        // it continues where it stopped (no extra user message).
        messages.push({ role: 'assistant', content: response.content })
        continue
      case 'end_turn':
        // Answered in text instead of calling the tool: remind it once.
        if (reminded) throw new Error('Claude did not return results')
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

/** A short, safe error text for the database and the browser (no secrets). */
function describeError(error: unknown): string {
  if (error instanceof Anthropic.RateLimitError) return 'Claude API: rate limited (429), try again later'
  if (error instanceof Anthropic.AuthenticationError) return 'Claude API: the API key is invalid or missing'
  if (error instanceof Anthropic.APIConnectionTimeoutError) return 'Claude API: timed out'
  if (error instanceof Anthropic.APIError) return `Claude API error ${error.status ?? ''}`.trim()
  if (error instanceof Error) return error.message.slice(0, 300)
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
  const usage: Usage = { searches: 0, input: 0, output: 0 }
  const finish = (fields: Record<string, unknown>) =>
    admin
      .from('search_runs')
      .update({
        finished_at: new Date().toISOString(),
        searches_used: usage.searches,
        input_tokens: usage.input,
        output_tokens: usage.output,
        ...fields,
      })
      .eq('id', runId)

  try {
    const [{ data: profile }, { data: applied }] = await Promise.all([
      admin.from('search_profile').select('skills, seniority, cities, languages, preferred_roles, dealbreakers').maybeSingle(),
      admin.from('applications').select('company_name').limit(500),
    ])
    const appliedCompanies = [...new Set((applied ?? []).map((row: { company_name: string }) => row.company_name))]

    // 4. Claude + web search.
    const client = new Anthropic({ apiKey: anthropicKey })
    const raw = await askClaude(client, mode, params, profile as Profile | null, appliedCompanies, usage, startedAt)

    // 5. Validate and store. already_applied is set by a database trigger.
    const { results, dropped } = validateResults(raw, mode)
    let stored: unknown[] = []
    if (results.length > 0) {
      const { data, error } = await admin
        .from('search_results')
        .insert(results.map((result) => ({ ...result, run_id: runId })))
        .select('id, kind, title, company_name, city, remote, url, deadline, contact, reasons, score, already_applied')
      if (error) throw new Error(`Could not store the results: ${error.message}`)
      stored = (data ?? []).sort((a: { score: number }, b: { score: number }) => b.score - a.score)
    }
    await finish({ status: 'success' })
    console.log(`search run ${runId} (${mode}): ${stored.length} results, ${dropped} dropped, ` +
      `${usage.searches} searches, ${usage.input} in / ${usage.output} out tokens`)

    return json(200, {
      run_id: runId,
      results: stored,
      dropped,
      usage: { searches: usage.searches, input_tokens: usage.input, output_tokens: usage.output },
    })
  } catch (error) {
    const message = describeError(error)
    console.error(`search run ${runId} failed: ${message}`)
    await finish({ status: 'error', error: message })
    return json(502, { run_id: runId, error: message })
  }
})
