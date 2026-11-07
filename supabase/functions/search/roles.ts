// Adjacent role families for the AI search (after run 11: developer queries
// only, while junior testing, data, support and ERP jobs fit my profile too).
//
// One family = one kind of job next to development: the query wording per
// language (Hungarian and Serbian the way local job sites name it, English for
// remote and unknown cities), the profile roles it covers, and the title words
// that count as those roles in a local title ("adatelemző" = data analyst).
// Used by prompt.ts (query plan) and score.ts (role points, the multilingual
// bonus). A plain list, not AI: explainable and testable, extended when a run
// shows a new case. Pure (no imports), so it runs in the tests with Node.

export interface RoleFamily {
  key: string
  // HU/RS: the query with its job word; the place is added at the end
  // ("junior tesztelő állás Szeged"). EN: no job word; " remote" or " job <city>" is added.
  queries: { HU: string; RS: string; EN: string }
  roles: string[] // profile roles (preferred_roles) this family covers, lowercase
  titleWords: string[] // a title with one of these counts as one of the roles (matched folded, whole words)
  multilingual: boolean // support/consultant work: Hungarian + Serbian + English is a plus
}

// The order is the rotation order: run N searches families N*k ... N*k+k-1
// (k = adjacent queries per chunk), so consecutive runs cover different ones.
export const ROLE_FAMILIES: RoleFamily[] = [
  {
    key: 'testing',
    queries: { HU: 'junior tesztelő állás', RS: 'QA tester posao', EN: 'junior QA engineer' },
    roles: ['qa tester', 'test automation', 'game tester'],
    titleWords: ['tesztelő', 'tester', 'qa', 'quality assurance', 'test automation', 'tesztautomatizálás', 'testiranje'],
    multilingual: false,
  },
  {
    key: 'data',
    queries: { HU: 'adatelemző állás', RS: 'analitičar podataka posao', EN: 'junior data analyst' },
    roles: ['data analyst', 'bi analyst', 'sql developer'],
    titleWords: ['adatelemző', 'data analyst', 'analitičar podataka', 'bi analyst', 'bi developer', 'business intelligence',
      'power bi', 'sql developer', 'sql fejlesztő', 'sql programer'],
    multilingual: false,
  },
  {
    key: 'support',
    queries: { HU: 'IT support állás', RS: 'IT podrška posao', EN: 'junior IT support' },
    roles: ['it support', 'application support'],
    titleWords: ['support', 'helpdesk', 'help desk', 'service desk', 'podrška', 'támogatás'],
    multilingual: true,
  },
  {
    key: 'erp',
    queries: { HU: 'ERP tanácsadó junior', RS: 'ERP konsultant junior', EN: 'junior ERP consultant' },
    roles: ['erp consultant', 'dynamics', 'sap', 'implementation engineer'],
    titleWords: ['erp', 'tanácsadó', 'konsultant', 'consultant', 'dynamics', 'sap', 'implementation', 'implementációs'],
    multilingual: true,
  },
  {
    key: 'devops',
    queries: { HU: 'junior DevOps állás', RS: 'junior DevOps posao', EN: 'junior DevOps engineer' },
    roles: ['devops'],
    titleWords: ['devops'],
    multilingual: false,
  },
  {
    key: 'game',
    queries: { HU: 'Unity fejlesztő állás', RS: 'Unity developer posao', EN: 'junior Unity developer' },
    roles: ['unity developer', 'game tester'],
    titleWords: ['unity', 'game', 'játék', 'játéktesztelő', 'igre'],
    multilingual: false,
  },
]

/** How many adjacent-role queries a chunk with `searches` searches gets: 2 of 4, 1 of 2-3. */
export function adjacentSlots(searches: number): number {
  if (searches >= 4) return 2
  return searches >= 2 ? 1 : 0
}

/**
 * The families one run searches: `slots` consecutive families starting at
 * rotation * slots (wrapping). rotation = the run's id, so run 7 and run 8
 * never search the same family (as long as 2 * slots <= the family count),
 * and every family comes round within a few runs.
 */
export function familiesFor(rotation: number, slots: number): RoleFamily[] {
  const n = ROLE_FAMILIES.length
  const start = ((Math.trunc(rotation) * slots) % n + n) % n
  return Array.from({ length: Math.min(slots, n) }, (_, i) => ROLE_FAMILIES[(start + i) % n])
}

/** One family's query for a place: a HU/RS city in its language, remote/unknown in English. */
export function familyQuery(family: RoleFamily, country: string, place: string | null): string {
  if (place === null) return `${family.queries.EN} remote`
  if (country === 'HU' || country === 'RS') return `${family.queries[country]} ${place}`
  return `${family.queries.EN} job ${place}`
}
