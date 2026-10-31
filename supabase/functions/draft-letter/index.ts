// Edge Function "draft-letter" (Phase 9.5): a short application letter.
//
// Browser (logged in) --POST {application_id | search_result_id, language}--> this function
//     -> my template (letter_templates) + ONE tailored sentence from Claude
//     -> {subject, body, language, suggested CV, contact}
//
// The letter is only returned and shown: I copy it or open it in my mail
// program and send it myself. Nothing here sends e-mail.
//
// Same security shape as "search": the ANTHROPIC_API_KEY is a Supabase secret
// read only here; the caller must be a logged-in user (no token -> 401); the
// service_role client exists only inside the function. Claude sees what our
// database has (posting text, match reasons, the posting's requirements). The
// only web access: when an application has no requirements yet but has a
// posting_url, Claude opens that ONE page with web_fetch (LinkedIn blocked,
// 8000 tokens), reports its requirements + a company sentence, and we save
// them on the application, so the page is opened once, not per letter.
// I pick the language on the dashboard; the CV follows from it (letter.ts).
// The tailored part (1-2 sentences) must be in that language, name one of my
// real projects and claim no years/seniority: otherwise Claude is asked once
// more; if it fails again (or Claude is not available), a rule-based sentence
// about the best-fitting real project is used (Claude is optional, like in
// the pipeline), and line_by says which.

import Anthropic from 'npm:@anthropic-ai/sdk@1.3.0'
import { createClient } from 'npm:@supabase/supabase-js@2.118.0'
import {
  FACTS_SYSTEM_PROMPT, FACTS_TOOL, type Kind, LINE_SCHEMA, MAX_LINE_SENTENCES, MAX_SENTENCES, MODEL, SYSTEM_PROMPT,
  buildLinePrompt, canFetch, cleanFacts, countSentences, factsPrompt, fallbackLine, fillTemplate, firstSentences,
  isEmail, joinSkills, lineProblem, matchedSkills, parseProjects, postingFetchTool, problemMessage, skillsForLetter,
  suggestCv, unknownPlaceholders, validateDraftRequest,
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
      { timeout: 40_000, maxRetries: 1 },
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

/** Claude's one sentence + the tokens used. Throws on any API problem. */
async function askForLine(
  client: Anthropic, messages: Anthropic.Messages.MessageParam[],
): Promise<{ line: string; text: string; input: number; output: number }> {
  const response = await client.messages.create(
    {
      model: MODEL,
      max_tokens: 2000,
      // low effort: one short sentence; structured output guarantees the JSON shape.
      output_config: { effort: 'low', format: { type: 'json_schema', schema: LINE_SCHEMA } },
      system: SYSTEM_PROMPT,
      messages,
    },
    // A timeout and one retry (HTTP rule of the project); the function stays well under 150 s.
    { timeout: 30_000, maxRetries: 1 },
  )
  const input = response.usage.input_tokens + (response.usage.cache_creation_input_tokens ?? 0) +
    (response.usage.cache_read_input_tokens ?? 0)
  const output = response.usage.output_tokens
  if (response.stop_reason === 'refusal') throw Object.assign(new Error('Claude declined'), { input, output })
  const text = response.content.find((block): block is Anthropic.Messages.TextBlock => block.type === 'text')?.text ?? ''
  const line = firstSentences((JSON.parse(text) as { tailored_line?: unknown }).tailored_line, MAX_LINE_SENTENCES)
  if (!line) throw Object.assign(new Error('Claude returned an empty line'), { input, output })
  return { line, text, input, output }
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

  // 4. Rules: CV (from my language choice), kind, template.
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

  // 5. The posting's facts: stored ones, or (application with a posting_url,
  // never looked at) open the page once and save what it says.
  const { data: profile } = await admin.from('search_profile').select('skills, projects').maybeSingle()
  const mySkills = (profile?.skills ?? {}) as Record<string, number>
  const projects = parseProjects(profile?.projects)
  const tokens = { input: 0, output: 0 }
  const apiKey = Deno.env.get('ANTHROPIC_API_KEY')
  const client = apiKey ? new Anthropic({ apiKey }) : null
  let factsBy: 'stored' | 'fetched' | 'none' = source.requirements?.length || source.companySummary ? 'stored' : 'none'
  if (client && source.applicationId !== null && source.requirements === null && canFetch(source.url)) {
    try {
      const { facts, input, output } = await extractFacts(client, source.url!)
      tokens.input += input
      tokens.output += output
      if (facts) {
        source.requirements = facts.requirements
        source.companySummary = facts.companySummary
        factsBy = 'fetched'
        const { error } = await admin.from('applications')
          .update({ requirements: facts.requirements, company_summary: facts.companySummary })
          .eq('id', source.applicationId)
        if (error) console.error(`draft-letter: could not save the posting facts (${error.message})`)
      }
    } catch (error) {
      console.error(`draft-letter: could not read the posting (${describeError(error)})`)
    }
  }
  const requirements = source.requirements ?? []

  // 6. The tailored part (1-2 sentences): Claude, checked, or the rule-based fallback.
  const prompt = buildLinePrompt({
    language, kind, company: source.company, position: source.position, city: source.city,
    reasons: source.reasons, postingText: source.postingText, mySkills, requirements,
    companySummary: source.companySummary, projects,
  })
  let line = ''
  let lineBy: 'claude' | 'rules' = 'claude'
  let claudeError: string | null = null
  try {
    if (!client) throw new Error('ANTHROPIC_API_KEY is not set')
    const messages: Anthropic.Messages.MessageParam[] = [{ role: 'user', content: prompt }]
    // At most two requests: the second only when the first line fails a check.
    for (let attempt = 1; attempt <= 2; attempt++) {
      const answer = await askForLine(client, messages)
      tokens.input += answer.input
      tokens.output += answer.output
      const problem = lineProblem(answer.line, language, projects)
      if (!problem) {
        line = answer.line
        break
      }
      console.log(`draft-letter: attempt ${attempt}: line rejected (${problem})`)
      messages.push({ role: 'assistant', content: answer.text })
      messages.push({ role: 'user', content: problemMessage(problem, language) })
    }
    if (!line) throw new Error('the line failed the checks twice')
  } catch (error) {
    const spent = error as { input?: number; output?: number }
    tokens.input += spent.input ?? 0
    tokens.output += spent.output ?? 0
    claudeError = describeError(error)
    console.error(`draft-letter: no usable Claude line, rule-based line instead (${claudeError})`)
    line = fallbackLine(language, matchedSkills(source.reasons), projects, requirements)
    lineBy = 'rules'
  }

  // 7. Fill and check the limit. Skills: the ones the posting matched first, max 4.
  // Two tailored sentences that push the letter over 5: keep only the first.
  const values = {
    company: source.company,
    position: source.position ?? '',
    skills: joinSkills(skillsForLetter(source.reasons, mySkills), language),
    tailored_line: line,
  }
  const subject = fillTemplate(template.subject, values, language)
  let letter = fillTemplate(template.body, values, language)
  if (countSentences(letter) > MAX_SENTENCES) {
    values.tailored_line = firstSentences(line, 1)
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
    `${language}, ${kind}, CV ${cv}, facts ${factsBy} (${requirements.length} requirements), line by ${lineBy}, ` +
    `${sentences} sentences, ` +
    `${tokens.input} in / ${tokens.output} out tokens`)

  return json(200, {
    subject,
    body: letter,
    language,
    kind,
    cv_variant: cv,
    contact: source.contact,
    contact_is_email: isEmail(source.contact),
    line_by: lineBy,
    sentences,
    requirements,
    company_summary: source.companySummary,
    facts_by: factsBy,
  })
})
