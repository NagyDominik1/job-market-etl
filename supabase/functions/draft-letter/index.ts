// Edge Function "draft-letter" (Phase 9.5): a short application letter.
//
// Browser (logged in) --POST {application_id | search_result_id, language?}--> this function
//     -> my template (letter_templates) + ONE tailored sentence from Claude
//     -> {subject, body, language + reason, suggested CV, contact}
//
// The letter is only returned and shown: I copy it or open it in my mail
// program and send it myself. Nothing here sends e-mail.
//
// Same security shape as "search": the ANTHROPIC_API_KEY is a Supabase secret
// read only here; the caller must be a logged-in user (no token -> 401); the
// service_role client exists only inside the function. No web tools: Claude
// only sees what our database already has (the posting text, match reasons).
// Language and CV are decided by plain rules (letter.ts); if Claude fails, a
// rule-based line from the match reasons is used (Claude is optional, like
// in the pipeline), and line_by says which.

import Anthropic from 'npm:@anthropic-ai/sdk@1.3.0'
import { createClient } from 'npm:@supabase/supabase-js@2.118.0'
import {
  type Kind, LINE_SCHEMA, MAX_SENTENCES, MODEL, SYSTEM_PROMPT, buildLinePrompt, chooseLanguage,
  countSentences, fallbackLine, fillTemplate, isEmail, matchedSkills, oneSentence, suggestCv, unknownPlaceholders,
  validateDraftRequest,
} from './letter.ts'

const MAX_DRAFTS_PER_HOUR = 30 // a cost cap against a stuck button or a loop (one letter ~ a fraction of a cent)

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' } })
}

// New-style keys (a JSON map) when the project has them, else the legacy
// ones. Read here, never logged, never returned. (Same as in "search".)
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

const KEY_PATTERN = /sk-ant-[A-Za-z0-9_-]+/g

/** A short, safe error text (never headers or the key). */
function describeError(error: unknown): string {
  if (error instanceof Anthropic.APIError) {
    const body = (error.error ?? {}) as { error?: { type?: unknown; message?: unknown } }
    const type = typeof body.error?.type === 'string' ? body.error.type : 'api_error'
    const message = typeof body.error?.message === 'string' ? body.error.message : error.message
    return `Claude API ${error.status ?? ''} ${type}: ${message}`.replace(KEY_PATTERN, '[redacted]').slice(0, 400)
  }
  if (error instanceof Error) return error.message.replace(KEY_PATTERN, '[redacted]').slice(0, 300)
  return 'Unknown error'
}

type Admin = ReturnType<typeof createClient>

/** What the letter is about, from an application or a search result. */
interface Source {
  applicationId: number | null
  searchResultId: number | null
  company: string
  companyKey: string
  position: string | null
  city: string | null
  url: string | null
  remote: boolean
  applicationLanguage: string | null
  reasons: string[]
  postingText: string
  contact: string | null
}

async function loadSource(admin: Admin, applicationId: number | null, searchResultId: number | null): Promise<Source | null> {
  if (searchResultId !== null) {
    const { data: r } = await admin.from('search_results')
      .select('id, kind, title, company_name, company_key, city, remote, url, contact, reasons')
      .eq('id', searchResultId).maybeSingle()
    if (!r) return null
    return {
      applicationId: null, searchResultId: r.id, company: r.company_name, companyKey: r.company_key,
      position: r.kind === 'job' ? r.title : null, city: r.city, url: r.url, remote: r.remote === true,
      applicationLanguage: null, reasons: r.reasons ?? [], postingText: r.title ?? '', contact: r.contact,
    }
  }
  const { data: a } = await admin.from('applications')
    .select('id, company_name, company_key, position, city, posting_url, language, job_id')
    .eq('id', applicationId).maybeSingle()
  if (!a) return null
  let reasons: string[] = []
  let postingText = a.position ?? ''
  let remote = false
  let city = a.city
  if (a.job_id) {
    // Added from the Job feed: the pipeline has the posting and its match reasons.
    const [{ data: job }, { data: enrichment }] = await Promise.all([
      admin.from('jobs').select('title, location, remote, description').eq('id', a.job_id).maybeSingle(),
      admin.from('job_enrichment').select('match_reasons').eq('job_id', a.job_id).maybeSingle(),
    ])
    if (job) {
      postingText = [job.title, job.description].filter(Boolean).join('\n')
      remote = job.remote === true
      city = city ?? job.location
    }
    reasons = enrichment?.match_reasons ?? []
  } else if (a.posting_url) {
    // Added from Keresés: the newest search result with the same link has the reasons.
    const { data: rows } = await admin.from('search_results').select('title, remote, reasons')
      .eq('url', a.posting_url).order('id', { ascending: false }).limit(1)
    if (rows?.[0]) {
      reasons = rows[0].reasons ?? []
      remote = rows[0].remote === true
    }
  }
  // A careers e-mail or page found by a company search, if any.
  const { data: contacts } = await admin.from('search_results').select('contact')
    .eq('company_key', a.company_key).not('contact', 'is', null).order('id', { ascending: false }).limit(1)
  return {
    applicationId: a.id, searchResultId: null, company: a.company_name, companyKey: a.company_key,
    position: a.position, city, url: a.posting_url, remote, applicationLanguage: a.language, reasons, postingText,
    contact: contacts?.[0]?.contact ?? null,
  }
}

/** Claude's one sentence + the tokens used. Throws on any API problem. */
async function askForLine(
  apiKey: string, prompt: string,
): Promise<{ line: string; input: number; output: number }> {
  const client = new Anthropic({ apiKey })
  const response = await client.messages.create(
    {
      model: MODEL,
      max_tokens: 2000,
      // low effort: one short sentence; structured output guarantees the JSON shape.
      output_config: { effort: 'low', format: { type: 'json_schema', schema: LINE_SCHEMA } },
      system: SYSTEM_PROMPT,
      messages: [{ role: 'user', content: prompt }],
    },
    // A timeout and one retry (HTTP rule of the project); the function stays well under 150 s.
    { timeout: 30_000, maxRetries: 1 },
  )
  const input = response.usage.input_tokens + (response.usage.cache_creation_input_tokens ?? 0) +
    (response.usage.cache_read_input_tokens ?? 0)
  const output = response.usage.output_tokens
  if (response.stop_reason === 'refusal') throw Object.assign(new Error('Claude declined'), { input, output })
  const text = response.content.find((block): block is Anthropic.Messages.TextBlock => block.type === 'text')?.text ?? ''
  const line = oneSentence((JSON.parse(text) as { tailored_line?: unknown }).tailored_line)
  if (!line) throw Object.assign(new Error('Claude returned an empty line'), { input, output })
  return { line, input, output }
}

Deno.serve(async (req: Request) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS_HEADERS })
  if (req.method !== 'POST') return json(405, { error: 'Use POST' })

  const supabaseUrl = Deno.env.get('SUPABASE_URL')!

  // 1. Who is calling? (the same check as "search")
  const token = (req.headers.get('Authorization') ?? '').replace(/^Bearer\s+/i, '')
  const authClient = createClient(supabaseUrl, supabaseKey('SUPABASE_PUBLISHABLE_KEYS', 'SUPABASE_ANON_KEY'), {
    auth: { persistSession: false, autoRefreshToken: false },
  })
  const { data: userData, error: userError } = await authClient.auth.getUser(token)
  if (userError || !userData.user) return json(401, { error: 'Log in first' })

  // 2. The request.
  let body: unknown
  try {
    body = await req.json()
  } catch {
    return json(400, { error: 'The body must be JSON' })
  }
  const request = validateDraftRequest(body)
  if (!request.ok) return json(400, { error: request.error })
  const { applicationId, searchResultId, language: chosen } = request.value

  const admin = createClient(supabaseUrl, supabaseKey('SUPABASE_SECRET_KEYS', 'SUPABASE_SERVICE_ROLE_KEY'), {
    auth: { persistSession: false, autoRefreshToken: false },
  })

  // 3. Cost cap: a simple count (not race-free like the search limit; a few
  // letters over the cap cost almost nothing, a loop would be stopped).
  const hourAgo = new Date(Date.now() - 3_600_000).toISOString()
  const { count } = await admin.from('letter_drafts').select('id', { count: 'exact', head: true }).gte('created_at', hourAgo)
  if ((count ?? 0) >= MAX_DRAFTS_PER_HOUR) return json(429, { error: `At most ${MAX_DRAFTS_PER_HOUR} letters per hour` })

  const source = await loadSource(admin, applicationId, searchResultId)
  if (!source) return json(404, { error: 'No such application or search result' })

  // 4. Rules: language, CV, kind, template.
  const { language, reason } = chooseLanguage({
    chosen, applicationLanguage: source.applicationLanguage, city: source.city, url: source.url,
    remote: source.remote, text: source.postingText,
  })
  const kind: Kind = source.position ? 'posting' : 'open'
  const cv = suggestCv(language)
  const { data: template } = await admin.from('letter_templates').select('subject, body')
    .eq('language', language).eq('kind', kind).maybeSingle()
  const log = (fields: Record<string, unknown>) =>
    admin.from('letter_drafts').insert({
      application_id: source.applicationId, search_result_id: source.searchResultId, language, kind, cv_variant: cv,
      ...fields,
    })
  if (!template) {
    return json(409, { error: `Nincs sablon (${language} / ${kind === 'posting' ? 'hirdetésre' : 'nyílt'}): töltsd ki a Sablonok fülön.` })
  }
  const unknown = unknownPlaceholders(`${template.subject} ${template.body}`)
  if (unknown.length) return json(422, { error: `Ismeretlen helyőrző a sablonban: {${unknown.join('}, {')}}` })

  // 5. The tailored line: Claude, or the rule-based fallback.
  const { data: profile } = await admin.from('search_profile').select('skills').maybeSingle()
  const prompt = buildLinePrompt({
    language, kind, company: source.company, position: source.position, city: source.city,
    reasons: source.reasons, postingText: source.postingText, mySkills: (profile?.skills ?? {}) as Record<string, number>,
  })
  let line = ''
  let lineBy: 'claude' | 'rules' = 'claude'
  let tokens = { input: 0, output: 0 }
  let claudeError: string | null = null
  const apiKey = Deno.env.get('ANTHROPIC_API_KEY')
  try {
    if (!apiKey) throw new Error('ANTHROPIC_API_KEY is not set')
    const answer = await askForLine(apiKey, prompt)
    line = answer.line
    tokens = { input: answer.input, output: answer.output }
  } catch (error) {
    const spent = error as { input?: number; output?: number }
    tokens = { input: spent.input ?? 0, output: spent.output ?? 0 }
    claudeError = describeError(error)
    console.error(`draft-letter: Claude failed, rule-based line instead (${claudeError})`)
    line = fallbackLine(language, matchedSkills(source.reasons))
    lineBy = 'rules'
  }

  // 6. Fill and check the limit.
  const values = { company: source.company, position: source.position ?? '', tailored_line: line }
  const subject = fillTemplate(template.subject, values)
  const letter = fillTemplate(template.body, values)
  const sentences = countSentences(letter)
  if (sentences > MAX_SENTENCES) {
    await log({ status: 'error', input_tokens: tokens.input, output_tokens: tokens.output, error: `${sentences} sentences` })
    return json(422, { error: `A levél ${sentences} mondat (max. ${MAX_SENTENCES}): rövidítsd a sablont a Sablonok fülön.` })
  }

  const { error: logError } = await log({
    status: 'success', input_tokens: tokens.input, output_tokens: tokens.output, error: claudeError,
  })
  if (logError) console.error(`draft-letter: could not log the draft (${logError.message})`)
  console.log(`draft-letter ${applicationId ? `application ${applicationId}` : `search result ${searchResultId}`}: ` +
    `${language} (${reason}), ${kind}, CV ${cv}, line by ${lineBy}, ${sentences} sentences, ` +
    `${tokens.input} in / ${tokens.output} out tokens`)

  return json(200, {
    subject,
    body: letter,
    language,
    language_reason: reason,
    kind,
    cv_variant: cv,
    contact: source.contact,
    contact_is_email: isEmail(source.contact),
    line_by: lineBy,
    sentences,
  })
})
