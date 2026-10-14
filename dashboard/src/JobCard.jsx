const DAY_MS = 24 * 60 * 60 * 1000

function formatDate(value) {
  return value ? new Date(value).toLocaleDateString('hu-HU') : null
}

// Score colour: green from 70, amber from 40, otherwise grey.
function scoreClass(score) {
  if (score == null) return 'score none'
  if (score >= 70) return 'score high'
  if (score >= 40) return 'score mid'
  return 'score low'
}

// The reasons start with "+", "-" or "~" (see src/scoring.py).
function reasonClass(reason) {
  if (reason.startsWith('+')) return 'plus'
  if (reason.startsWith('-')) return 'minus'
  return 'neutral'
}

export default function JobCard({ job }) {
  const isNew = Date.now() - new Date(job.first_seen_at).getTime() < DAY_MS
  const deadline = formatDate(job.deadline)

  return (
    <article className="card">
      <header className="card-head">
        <span className={scoreClass(job.match_score)} title="Match score">
          {job.match_score ?? '–'}
        </span>
        <div>
          <h2>
            {job.url ? (
              // noopener noreferrer: the job site can't control this tab or see where I came from.
              <a href={job.url} target="_blank" rel="noopener noreferrer">
                {job.title}
              </a>
            ) : (
              job.title
            )}
            {isNew && <span className="badge">new</span>}
          </h2>
          <p className="meta">
            {job.company_name || 'Unknown company'}
            {job.location && ` · ${job.location}`}
            {job.remote && ' · remote'}
          </p>
        </div>
      </header>

      {job.match_reasons?.length > 0 && (
        <ul className="reasons">
          {job.match_reasons.map((reason) => (
            <li key={reason} className={reasonClass(reason)}>
              {reason}
            </li>
          ))}
        </ul>
      )}

      <footer className="card-foot">
        {deadline && <span>Deadline: {deadline}</span>}
        <span>First seen: {formatDate(job.first_seen_at)}</span>
        {job.sources && <span>Sources: {job.sources}</span>}
      </footer>
    </article>
  )
}
