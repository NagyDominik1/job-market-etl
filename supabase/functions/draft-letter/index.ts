// Edge Function "draft-letter" (Phase 9.5): a short application letter.
//
// Browser (logged in) --POST {application_id | search_result_id, language}--> this function
//     -> the body written by Claude (max 5 sentences, my real projects), framed by my
//        template's greeting and closing; the whole template is the fallback
//     -> {subject, body, language, suggested CV, contact}
//
// The letter is only returned and shown: I copy it or open it in my mail
// program and send it myself. Nothing here sends e-mail.
//
// Same security shape as "search": the ANTHROPIC_API_KEY is a Supabase secret
// read only here; the caller must be a logged-in user (no token -> 401); the
// service_role client exists only inside the function. Claude sees what our
// database has (posting text, match reasons, the posting's requirements). Web
// access, each at most once per application / search result (the result is saved):
//  - no requirements yet but a posting_url: Claude opens that ONE page with
//    web_fetch (LinkedIn blocked, 8000 tokens) and reports its requirements +
//    a company sentence;
//  - still no company sentence: the company's own website (one search to find
//    it, one page). Nothing found -> no company sentence (never invented).
// I pick the language on the dashboard; the CV follows from it (letter.ts).
// The body must pass the checks of letter.ts (bodyProblem): otherwise Claude
// is asked once more, with the reason; if it fails again (or Claude is not
// available), my template is filled by plain code (Claude is optional, like in
// the pipeline), and body_by says which.

import Anthropic from 'npm:@anthropic-ai/sdk@1.3.0'
import { createClient } from 'npm:@supabase/supabase-js@2.118.0'
import {
  BODY_SCHEMA, BODY_SYSTEM_PROMPT, COMPANY_SYSTEM_PROMPT, COMPANY_TOOL, FACTS_SYSTEM_PROMPT, FACTS_TOOL, type Kind,
  MAX_SENTENCES, MODEL, assembleLetter, bodyProblem, bodyProblemMessage, buildBodyPrompt, canFetch, cleanBody, cleanFacts,
  companyLookupTools, companyPrompt, countSentences, factsPrompt, fallbackLine, fillTemplate, frameOf, isEmail,
  joinSkills, matchedSkills, parseProjects, postingFetchTool, skillsForLetter, subjectFor, suggestCv,
  unknownPlaceholders, validateDraftRequest,
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
  reasons: string[]
  postingText: string
  contact: string | null
  requirements: string[] | null // null = an application whose posting was not looked at yet
  companySummary: string | null
}

async function loadSource(admin: Admin, applicationId: number | null, searchResultId: number | null): Promise<Source | null> {
  if (searchResultId !== null) {
    const { data: r } = await admin.from('search_results')
      .select('id, kind, title, company_name, company_key, city, remote, url, contact, reasons, requirements, company_summary')
      .eq('id', searchResultId).maybeSingle()
    if (!r) return null
    return {
      applicationId: null, searchResultId: r.id, company: r.company_name, companyKey: r.company_key,
      position: r.kind === 'job' ? r.title : null, city: r.city, url: r.url, remote: r.remote === true,
      reasons: r.reasons ?? [], postingText: r.title ?? '', contact: r.contact,
      requirements: r.requirements ?? [], companySummary: r.company_summary,
    }
  }
  const { data: a } = await admin.from('applications')
    .select('id, company_name, company_key, position, city, posting_url, job_id, requirements, company_summary')
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
    position: a.position, city, url: a.posting_url, remote, reasons, postingText,
    contact: contacts?.[0]?.contact ?? null,
    requirements: a.requirements, companySummary: a.company_summary,
  }
}

/**
 * Open the posting once and report its facts (9.5). Returns null facts when
 * the page could not be fetched (then nothing is saved and the next letter
 * may try again); a fetched page with no requirements gives [] (saved: no
 * second fetch). Tokens are returned for the cost log either way.
 */
async function extractFacts(
  client: Anthropic, url: string,
): Promise<{ facts: { requirements: string[]; companySummary: string | null } | null; input: number; output: number }> {
  const messages: Anthropic.Messages.MessageParam[] = [{ role: 'user', content: factsPrompt(url) }]
  let input = 0
  let output = 0
  let fetched = false
  for (let request = 0; request < 3; request++) {
    const response = await client.messages.create(
      {
        model: MODEL,
        max_tokens: 4000,
        output_config: { effort: 'low' },
        system: FACTS_SYSTEM_PROMPT,
        tools: [postingFetchTool(), FACTS_TOOL],
        messages,
      },
      { timeout: 40_000, maxRetries: 0 },
    )
    input += response.usage.input_tokens + (response.usage.cache_creation_input_tokens ?? 0) +
      (response.usage.cache_read_input_tokens ?? 0)
    output += response.usage.output_tokens
    for (const block of response.content as unknown as Array<Record<string, unknown>>) {
      const content = block.content as { type?: string } | undefined
      if (block.type === 'web_fetch_tool_result' && content?.type !== 'web_fetch_tool_result_error') fetched = true
    }
    const call = response.content.find(
      (block): block is Anthropic.Messages.ToolUseBlock => block.type === 'tool_use' && block.name === 'submit_posting_facts',
    )
    if (call) {
      const raw = call.input as { requirements?: unknown; company_summary?: unknown }
      return { facts: fetched ? cleanFacts(raw.requirements, raw.company_summary) : null, input, output }
    }
    if (response.stop_reason !== 'pause_turn') break
    messages.push({ role: 'assistant', content: response.content })
  }
  return { facts: null, input, output }
}

/**
 * The company's own website, once (when no company fact is stored): at most
 * one search + one page. summary null = the lookup could not finish (an
 * error, or the page failed to load: nothing saved, the next letter may try
 * again); '' = looked, nothing found (saved: no second lookup).
 */
async function lookupCompany(
  client: Anthropic, prompt: string, timeout: number,
): Promise<{ summary: string | null; input: number; output: number }> {
  const messages: Anthropic.Messages.MessageParam[] = [{ role: 'user', content: prompt }]
  let input = 0
  let output = 0
  let fetchOk = false
  let fetchFailed = false
  for (let request = 0; request < 3; request++) {
    const response = await client.messages.create(
      {
        model: MODEL,
        max_tokens: 3000,
        output_config: { effort: 'low' },
        system: COMPANY_SYSTEM_PROMPT,
        tools: [...companyLookupTools(), COMPANY_TOOL],
        messages,
      },
      { timeout, maxRetries: 0 },
    )
    input += response.usage.input_tokens + (response.usage.cache_creation_input_tokens ?? 0) +
      (response.usage.cache_read_input_tokens ?? 0)
    output += response.usage.output_tokens
    for (const block of response.content as unknown as Array<Record<string, unknown>>) {
      const content = block.content as { type?: string } | undefined
      if (block.type !== 'web_fetch_tool_result') continue
      if (content?.type === 'web_fetch_tool_result_error') fetchFailed = true
      else fetchOk = true
    }
    const call = response.content.find(
      (block): block is Anthropic.Messages.ToolUseBlock => block.type === 'tool_use' && block.name === 'submit_company_fact',
    )
    if (call) {
      // A sentence counts only from a page that really loaded; no page tried = nothing found.
      const sentence = cleanFacts([], (call.input as { company_summary?: unknown }).company_summary).companySummary
      if (fetchOk) return { summary: sentence ?? '', input, output }
      return { summary: fetchFailed ? null : '', input, output }
    }
    if (response.stop_reason !== 'pause_turn') break
    messages.push({ role: 'assistant', content: response.content })
  }
  return { summary: null, input, output }
}

/** Claude's letter body + the tokens used. Throws on any API problem. */
async function askForBody(
  client: Anthropic, messages: Anthropic.Messages.MessageParam[], timeout: number,
): Promise<{ body: string; text: string; input: number; output: number }> {
  const response = await client.messages.create(
    {
      model: MODEL,
      max_tokens: 3000,
      // low effort: a short paragraph; structured output guarantees the JSON shape.
      output_config: { effort: 'low', format: { type: 'json_schema', schema: BODY_SCHEMA } },
      system: BODY_SYSTEM_PROMPT,
      messages,
    },
    // No SDK retry: our own second attempt (after a failed check) is the retry.
    { timeout, maxRetries: 0 },
  )
  const input = response.usage.input_tokens + (response.usage.cache_creation_input_tokens ?? 0) +
    (response.usage.cache_read_input_tokens ?? 0)
  const output = response.usage.output_tokens
  if (response.stop_reason === 'refusal') throw Object.assign(new Error('Claude declined'), { input, output })
  const text = response.content.find((block): block is Anthropic.Messages.TextBlock => block.type === 'text')?.text ?? ''
  const body = cleanBody((JSON.parse(text) as { body?: unknown }).body)
  if (!body) throw Object.assign(new Error('Claude returned an empty body'), { input, output })
  return { body, text, input, output }
}

Deno.serve(async (req: Request) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS_HEADERS })
  if (req.method !== 'POST') return json(405, { error: 'Use POST' })

  // Supabase stops a function at 150 s: every Claude call gets what is left
  // of a 130 s budget, and an optional step is skipped when too little is left.
  const started = Date.now()
  const left = () => 130_000 - (Date.now() - started)
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
  const { applicationId, searchResultId, language } = request.value

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

  // 4. Rules: CV (from my language choice), kind, subject, template (its
  // greeting and closing frame Claude's body; the whole template is the fallback).
  const kind: Kind = source.position ? 'posting' : 'open'
  const cv = suggestCv(language)
  const subject = subjectFor(language, kind, source.position)
  const { data: template } = await admin.from('letter_templates').select('body')
    .eq('language', language).eq('kind', kind).maybeSingle()
  const log = (fields: Record<string, unknown>) =>
    admin.from('letter_drafts').insert({
      application_id: source.applicationId, search_result_id: source.searchResultId, language, kind, cv_variant: cv,
      ...fields,
    })

  // 5. The posting's facts: stored ones, or (application with a posting_url,
  // never looked at) open the page once and save what it says.
  const { data: profile } = await admin.from('search_profile').select('skills, projects').maybeSingle()
  const mySkills = (profile?.skills ?? {}) as Record<string, number>
  const projects = parseProjects(profile?.projects)
  const tokens = { input: 0, output: 0 }
  const apiKey = Deno.env.get('ANTHROPIC_API_KEY')
  const client = apiKey ? new Anthropic({ apiKey }) : null
  let factsBy: 'stored' | 'fetched' | 'none' = source.requirements?.length ? 'stored' : 'none'
  if (client && source.applicationId !== null && source.requirements === null && canFetch(source.url)) {
    try {
      const { facts, input, output } = await extractFacts(client, source.url!)
      tokens.input += input
      tokens.output += output
      if (facts) {
        source.requirements = facts.requirements
        source.companySummary = source.companySummary || facts.companySummary
        factsBy = 'fetched'
        const { error } = await admin.from('applications')
          .update({ requirements: facts.requirements, company_summary: source.companySummary })
          .eq('id', source.applicationId)
        if (error) console.error(`draft-letter: could not save the posting facts (${error.message})`)
      }
    } catch (error) {
      console.error(`draft-letter: could not read the posting (${describeError(error)})`)
    }
  }
  const requirements = source.requirements ?? []

  // 6. The company sentence: the stored fact, or the company's own website,
  // looked up once (NULL = never looked; '' = looked, nothing found). Saved on
  // the application or the search result, so the site is not opened again.
  let companyBy: 'stored' | 'fetched' | 'none' = source.companySummary ? 'stored' : 'none'
  if (client && source.companySummary === null && left() > 75_000) {
    const hints = [source.url, source.contact].filter((url): url is string => canFetch(url))
    try {
      const { summary, input, output } = await lookupCompany(
        client, companyPrompt(source.company, source.city, hints), Math.min(35_000, left() - 40_000))
      tokens.input += input
      tokens.output += output
      if (summary !== null) {
        source.companySummary = summary
        if (summary) companyBy = 'fetched'
        const { error } = source.applicationId !== null
          ? await admin.from('applications').update({ company_summary: summary }).eq('id', source.applicationId)
          : await admin.from('search_results').update({ company_summary: summary }).eq('id', source.searchResultId)
        if (error) console.error(`draft-letter: could not save the company fact (${error.message})`)
      }
    } catch (error) {
      console.error(`draft-letter: could not look up the company (${describeError(error)})`)
    }
  }
  const companySummary = source.companySummary || null

  // 7. The body: Claude, checked; one more try with the reason; else my template.
  const { greeting, closing } = frameOf(template?.body ?? null, language)
  const prompt = buildBodyPrompt({
    language, kind, company: source.company, position: source.position, city: source.city,
    reasons: source.reasons, postingText: source.postingText, mySkills, requirements, companySummary, projects,
  })
  let letter = ''
  let bodyBy: 'claude' | 'template' = 'claude'
  let claudeError: string | null = null
  try {
    if (!client) throw new Error('ANTHROPIC_API_KEY is not set')
    const messages: Anthropic.Messages.MessageParam[] = [{ role: 'user', content: prompt }]
    for (let attempt = 1; attempt <= 2 && left() > 20_000; attempt++) {
      const answer = await askForBody(client, messages, Math.min(30_000, left() - 5_000))
      tokens.input += answer.input
      tokens.output += answer.output
      const problem = bodyProblem(answer.body, language, projects, Object.keys(mySkills))
      if (!problem) {
        letter = assembleLetter(greeting, answer.body, closing)
        break
      }
      console.log(`draft-letter: attempt ${attempt}: body rejected (${problem})`)
      messages.push({ role: 'assistant', content: answer.text })
      messages.push({ role: 'user', content: bodyProblemMessage(problem, language) })
    }
    if (!letter) throw new Error('the body failed the checks twice')
  } catch (error) {
    const spent = error as { input?: number; output?: number }
    tokens.input += spent.input ?? 0
    tokens.output += spent.output ?? 0
    claudeError = describeError(error)
    bodyBy = 'template'
    console.error(`draft-letter: no usable Claude body, template instead (${claudeError})`)
  }

  if (bodyBy === 'template') {
    // The template, filled by plain code (the letter before this change).
    if (!template) {
      return json(409, { error: `Nincs sablon (${language} / ${kind === 'posting' ? 'hirdetésre' : 'nyílt'}): töltsd ki a Sablonok fülön.` })
    }
    const unknown = unknownPlaceholders(template.body)
    if (unknown.length) return json(422, { error: `Ismeretlen helyőrző a sablonban: {${unknown.join('}, {')}}` })
    const values = {
      company: source.company,
      position: source.position ?? '',
      skills: joinSkills(skillsForLetter(source.reasons, mySkills), language),
      tailored_line: fallbackLine(language, matchedSkills(source.reasons), projects, requirements),
    }
    letter = fillTemplate(template.body, values, language)
  }
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
    `${language}, ${kind}, CV ${cv}, facts ${factsBy} (${requirements.length} requirements), company ${companyBy}, ` +
    `body by ${bodyBy}, ${sentences} sentences, ${tokens.input} in / ${tokens.output} out tokens, ` +
    `${Math.round((Date.now() - started) / 1000)} s`)

  return json(200, {
    subject,
    body: letter,
    language,
    kind,
    cv_variant: cv,
    contact: source.contact,
    contact_is_email: isEmail(source.contact),
    body_by: bodyBy,
    sentences,
    requirements,
    company_summary: companySummary,
    facts_by: factsBy,
    company_by: companyBy,
  })
})
