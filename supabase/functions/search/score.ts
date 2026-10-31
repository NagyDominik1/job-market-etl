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
// Skills match like the Python version (src/vocabulary.py): spelling aliases
// ("JS" = JavaScript, "postgres" = PostgreSQL), "HTML/CSS" in my profile
// counts for HTML and CSS. One addition the pipeline doesn't have (its files
// stay unchanged): SQL dialects count for "SQL" and the other way round.
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

// Common spelling variants -> one canonical name. The same entries as
// SKILL_ALIASES in src/vocabulary.py (a test keeps them in sync), plus a few
// SQL dialects for SQL_FAMILY. Keys are lowercase.
export const SKILL_ALIASES: Record<string, string> = {
  'python': 'Python',
  'postgres': 'PostgreSQL',
  'postgresql': 'PostgreSQL',
  'sql': 'SQL',
  'js': 'JavaScript',
  'javascript': 'JavaScript',
  'ts': 'TypeScript',
  'typescript': 'TypeScript',
  'react': 'React',
  'reactjs': 'React',
  'react.js': 'React',
  'node': 'Node.js',
  'nodejs': 'Node.js',
  'node.js': 'Node.js',
  'golang': 'Go',
  'k8s': 'Kubernetes',
  'kubernetes': 'Kubernetes',
  'docker': 'Docker',
  'aws': 'AWS',
  'amazon web services': 'AWS',
  'gcp': 'GCP',
  'google cloud': 'GCP',
  'azure': 'Azure',
  'microsoft azure': 'Azure',
  'java': 'Java',
  'c#': 'C#',
  'csharp': 'C#',
  '.net': '.NET',
  'dotnet': '.NET',
  'asp.net': '.NET',
  '.net core': '.NET',
  'php': 'PHP',
  'mysql': 'MySQL',
  'mariadb': 'MySQL',
  'rest': 'REST',
  'rest api': 'REST',
  'restful': 'REST',
  'restful api': 'REST',
  'html': 'HTML',
  'html5': 'HTML',
  'css': 'CSS',
  'css3': 'CSS',
  'git': 'Git',
  'ci/cd': 'CI/CD',
  'unity': 'Unity',
  'unity3d': 'Unity',
  'excel': 'Excel',
  'ms excel': 'Excel',
  'microsoft excel': 'Excel',
  // search only (not in the pipeline's list): SQL dialects
  'mssql': 'SQL Server',
  'ms sql': 'SQL Server',
  'sql server': 'SQL Server',
  't-sql': 'SQL Server',
  'sqlite': 'SQLite',
  'oracle': 'Oracle',
  'pl/sql': 'Oracle',
}

// A posting asking for MySQL fits someone who knows SQL, and "SQL" in a
// posting fits someone who knows MySQL: one counts for the other, both ways.
const SQL_FAMILY = new Set(['SQL', 'MySQL', 'PostgreSQL', 'SQL Server', 'SQLite', 'Oracle'])

/** The canonical spelling of a skill ("postgres" -> "PostgreSQL"); unknown names stay as written. */
export function canonicalSkill(skill: string): string {
  return SKILL_ALIASES[skill.trim().toLowerCase()] ?? skill.trim()
}

/**
 * The canonical skills one profile entry counts for, like skill_alternatives()
 * in src/vocabulary.py: "REST API" -> {REST}; "HTML/CSS" -> {HTML, CSS}
 * ("CI/CD" and "PL/SQL" are skill names, so they are not split).
 */
export function skillAlternatives(profileSkill: string): string[] {
  if (profileSkill.trim().toLowerCase() in SKILL_ALIASES || !profileSkill.includes('/')) return [canonicalSkill(profileSkill)]
  return profileSkill.split('/').filter((part) => part.trim()).map(canonicalSkill)
}

function scoreSkills(skills: string[], ctx: ScoringContext, weight: number): [number, string[]] {
  // canonical name (folded) -> [name as written in my profile, my level]
  const mine = new Map<string, [string, number]>()
  for (const [name, level] of Object.entries(ctx.skills)) {
    for (const alternative of skillAlternatives(name)) mine.set(fold(alternative), [name, level])
  }
  // My best SQL-family skill, for a posting's dialect I don't list myself.
  const sqlSkills = [...SQL_FAMILY].map((s) => mine.get(fold(s))).filter((hit) => hit !== undefined)
  const bestSql = sqlSkills.sort((a, b) => b[1] - a[1])[0]

  const unique = [...new Map(skills.map(canonicalSkill).filter(Boolean).map((s) => [fold(s), s])).values()]
  if (!unique.length) return [0, ['~ no skills listed']]
  const matched = new Map<string, [number, string]>() // profile name -> [level, the posting's skill if different]
  const missing: string[] = []
  for (const skill of unique) {
    const direct = mine.get(fold(skill))
    const hit = direct ?? (SQL_FAMILY.has(skill) ? bestSql : undefined)
    if (hit && !matched.has(hit[0])) matched.set(hit[0], [hit[1], direct ? '' : skill])
    else if (!hit) missing.push(skill)
  }
  const earned = [...matched.values()].reduce((sum, [level]) => sum + level / 3, 0)
  const points = weight * Math.min(1, earned / Math.min(unique.length, SKILLS_FOR_FULL_POINTS))
  // "+ SQL (2) for MySQL": the point came through the SQL family, not the same name.
  const reasons = [...matched].sort((a, b) => b[1][0] - a[1][0])
    .map(([name, [level, via]]) => `+ ${name} (${level})${via ? ` for ${via}` : ''}`)
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
