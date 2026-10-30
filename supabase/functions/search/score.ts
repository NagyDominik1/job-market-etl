// Score and reasons for AI search results, computed by OUR code (run 9).
//
// Claude only reports short facts (title, company, city, skills, level...);
// the judgement "how good is this for me" is plain code, like src/scoring.py
// for the pipeline: deterministic, free, testable, and every point explained.
// It also keeps the model's output small (no reasons to write per posting),
// which made long single requests shorter.
//
// Jobs (same weights as src/scoring.py): skills 40, seniority 25, city 20,
// role 15. Companies: city 30, stack 30, junior-friendly 20, careers contact 20.
// Simpler than the Python version: skills match by name (case-insensitive,
// "HTML/CSS" counts for HTML and CSS), no vocabulary of aliases.
// Pure (no Deno or npm imports), so it runs in the tests with Node.

export interface ScoringContext {
  skills: Record<string, number> // my skills, level 1-3
  seniority: string[] // the levels I look for
  cities: string[] // priority order (first = most wanted)
  roles: string[] // preferred roles, matched in the title
}

export const EMPTY_CONTEXT: ScoringContext = { skills: {}, seniority: [], cities: [], roles: [] }

const SKILLS_FOR_FULL_POINTS = 5
const LOWEST_PRIORITY_SHARE = 0.5
const SENIORITY_ORDER = ['intern', 'junior', 'mid', 'senior', 'lead']

// Same city aliases as src/normalize.py (the ones the search uses).
const CITY_ALIASES: Record<string, string> = {
  szabadka: 'subotica', ujvidek: 'novi sad', belgrade: 'beograd', belgrad: 'beograd',
}

/** Lowercase, no accents ("Újvidék" -> "ujvidek"; đ -> dj like the pipeline). */
export function fold(text: string): string {
  return text.toLowerCase().replace(/đ/g, 'dj').normalize('NFKD').replace(/\p{M}/gu, '').replace(/\s+/g, ' ').trim()
}

function cityKey(city: string): string {
  const folded = fold(city)
  if (['remote', 'tavmunka', 'rad od kuce', 'home office'].includes(folded)) return 'remote'
  return CITY_ALIASES[folded] ?? folded
}

/** "php" in "junior php developer" as whole words. */
function containsPhrase(text: string, phrase: string): boolean {
  if (!phrase) return false
  const escaped = phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`(?<![\\p{L}\\p{N}])${escaped}(?![\\p{L}\\p{N}])`, 'u').test(text)
}

function scoreSkills(skills: string[], ctx: ScoringContext, weight: number): [number, string[]] {
  const mine = new Map<string, [string, number]>()
  for (const [name, level] of Object.entries(ctx.skills)) {
    for (const part of [name, ...name.split('/')]) mine.set(fold(part), [name, level])
  }
  const unique = [...new Set(skills.map(fold).filter(Boolean))]
  if (!unique.length) return [0, ['~ no skills listed']]
  const matched = new Map<string, number>()
  const missing: string[] = []
  for (const skill of unique) {
    const hit = mine.get(skill)
    if (hit) matched.set(hit[0], hit[1])
    else missing.push(skill)
  }
  const earned = [...matched.values()].reduce((sum, level) => sum + level / 3, 0)
  const points = weight * Math.min(1, earned / Math.min(unique.length, SKILLS_FOR_FULL_POINTS))
  const reasons = [...matched].sort((a, b) => b[1] - a[1]).map(([name, level]) => `+ ${name} (${level})`)
  if (missing.length) reasons.push(`- not in my skills: ${missing.slice(0, 5).join(', ')}${missing.length > 5 ? ' ...' : ''}`)
  return [points, reasons]
}

function scoreSeniority(level: string, ctx: ScoringContext): [number, string[]] {
  const weight = 25
  if (!ctx.seniority.length) return [weight, []]
  if (ctx.seniority.includes(level)) return [weight, [`+ ${level}`]]
  if (!SENIORITY_ORDER.includes(level)) return [weight / 2, ['~ seniority unknown']]
  const distance = Math.min(...ctx.seniority.filter((l) => SENIORITY_ORDER.includes(l))
    .map((l) => Math.abs(SENIORITY_ORDER.indexOf(l) - SENIORITY_ORDER.indexOf(level))))
  if (distance === 1) return [10, [`~ ${level} (one level away)`]]
  return [0, [`- ${level}`]]
}

function scoreCity(city: string, remote: boolean, ctx: ScoringContext, weight: number): [number, string[]] {
  const key = remote ? 'remote' : cityKey(city)
  const mine = ctx.cities.map(cityKey)
  const index = mine.indexOf(key)
  if (key && index >= 0) {
    const share = 1 - (1 - LOWEST_PRIORITY_SHARE) * index / Math.max(mine.length - 1, 1)
    return [weight * share, [`+ ${key === 'remote' ? 'remote' : ctx.cities[index]} (priority ${index + 1})`]]
  }
  if (key === 'remote') return [weight * LOWEST_PRIORITY_SHARE, ['+ remote']]
  if (!key) return [0, ['~ location unknown']]
  return [0, [`- ${city}`]]
}

function scoreRole(title: string, ctx: ScoringContext): [number, string[]] {
  const folded = fold(title)
  const role = ctx.roles.find((r) => containsPhrase(folded, fold(r)))
  return role ? [15, [`+ role: ${role}`]] : [0, []]
}

/** The facts of one job result that the score uses. */
export interface JobFacts {
  title: string
  city: string
  remote: boolean
  seniority: string
  skills: string[]
}

export function scoreJob(job: JobFacts, ctx: ScoringContext): { score: number; reasons: string[] } {
  let total = 0
  const reasons: string[] = []
  for (const [points, why] of [
    scoreSkills(job.skills, ctx, 40),
    scoreSeniority(job.seniority, ctx),
    scoreCity(job.city, job.remote, ctx, 20),
    scoreRole(job.title, ctx),
  ]) {
    total += points
    reasons.push(...why)
  }
  return { score: Math.round(total), reasons }
}

export interface CompanyFacts {
  city: string
  remote: boolean
  skills: string[] // their tech stack
  juniorFriendly: boolean
  hasContact: boolean
}

export function scoreCompany(company: CompanyFacts, ctx: ScoringContext): { score: number; reasons: string[] } {
  const [cityPoints, cityReasons] = scoreCity(company.city, company.remote, ctx, 30)
  const [stackPoints, stackReasons] = scoreSkills(company.skills, ctx, 30)
  const juniorPoints = company.juniorFriendly ? 20 : 0
  const contactPoints = company.hasContact ? 20 : 0
  return {
    score: Math.round(cityPoints + stackPoints + juniorPoints + contactPoints),
    reasons: [
      ...cityReasons,
      ...stackReasons,
      company.juniorFriendly ? '+ junior-friendly' : '~ junior-friendliness unknown',
      company.hasContact ? '+ careers contact' : '- no careers contact found',
    ],
  }
}

/**
 * The context for one run: my skills from search_profile; cities, roles and
 * levels from the search form (the run's params, ALL cities, so a chunk's
 * city keeps its priority), falling back to the profile when the form left
 * them empty. Remote counts as the last city when the run includes remote.
 */
export function scoringContext(
  params: { cities: string[]; remote: boolean; roles: string[]; seniority: string[] },
  profile: { skills?: Record<string, number>; cities?: string[]; preferred_roles?: string[]; seniority?: string[] } | null,
): ScoringContext {
  const cities = params.cities.length ? [...params.cities, ...(params.remote ? ['remote'] : [])] : profile?.cities ?? []
  return {
    skills: profile?.skills ?? {},
    seniority: params.seniority.length ? params.seniority : profile?.seniority ?? [],
    cities,
    roles: params.roles.length ? params.roles : profile?.preferred_roles ?? [],
  }
}
