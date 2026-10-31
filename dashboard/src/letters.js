// Letter helpers for the dashboard (Phase 9.5). The Sablonok editor checks a
// template before saving with the SAME rules as the Edge Function
// (supabase/functions/draft-letter/letter.ts): known placeholders, at most 5
// sentences. A copy, because the dashboard and the function are built
// separately; the function checks again, so a drift can't produce a long letter.

export const MAX_SENTENCES = 5
export const PLACEHOLDERS = ['company', 'position', 'tailored_line']

export const LANGUAGE_LABELS = { hu: 'magyar', sr: 'szerb', en: 'angol' }
export const KIND_LABELS = { posting: 'hirdetésre', open: 'nyílt jelentkezés' }

export function unknownPlaceholders(text) {
  return [...text.matchAll(/\{([^{}]*)\}/g)].map((match) => match[1]).filter((name) => !PLACEHOLDERS.includes(name))
}

// Greeting line and closing lines don't count (see letter.ts countSentences).
export function countSentences(body) {
  const lines = body.split('\n').map((line) => line.trim()).filter(Boolean)
  if (lines.length > 1 && lines[0].length <= 60 && /[,!]$/.test(lines[0])) lines.shift()
  while (lines.length && !/[.!?]$/.test(lines[lines.length - 1])) lines.pop()
  return lines.join(' ').split(/(?<=[.!?])\s+/).filter((part) => /\p{L}/u.test(part)).length
}

/** {tailored_line} counts as one sentence. */
export function templateSentences(body) {
  return countSentences(body.replace(/\{tailored_line\}/g, 'X.'))
}
