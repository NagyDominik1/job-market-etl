# Project: job-market-etl v2
Personal job-posting monitor ETL pipeline and data engineering portfolio project.
I check ~5 Serbian and Hungarian IT job sites manually every day. This pipeline collects
postings, unifies them, removes duplicates, scores them against my profile, and shows
what's new.

## Hard constraints
- NO web scraping of job sites. Legal sources only: email job alerts (my own Gmail via IMAP),
  public company ATS APIs (Greenhouse/Lever/Workable etc.), manual CSV, Arbeitnow API.
- Never commit secrets. .env stays in .gitignore; keep .env.example up to date.
- Claude API is optional: if ANTHROPIC_API_KEY is missing or the call fails,
  fall back to rule-based enrichment. Record which method was used (enriched_by).
- Same code runs locally and in production; only env vars differ (DATABASE_URL etc.).

## Architecture
Extractors (email | ats | csv | arbeitnow) → raw_postings (raw JSONB)
→ transform (normalize, fingerprint, dedup) → enrichment (rules / Claude)
→ jobs + job_sources → "new postings" report
Local: Docker Compose (Postgres + pipeline), one command: docker compose up
Production (later): GitHub Actions daily run → Supabase Postgres → Vercel React dashboard

## Schema (migrations/001_initial_v2.sql + 002_transform.sql + 003_match_score.sql)
- sources (id, name UNIQUE, kind CHECK IN ('email','ats','csv','api')) — one row per
  extractor, inserted by the pipeline itself
- raw_postings (id, source_id FK, external_id, payload JSONB, fetched_at, last_seen_at,
  job_id FK -> jobs ON DELETE SET NULL (indexed), transform_error,
  UNIQUE(source_id, external_id)) — job_id NULL = not transformed yet
- jobs (id, fingerprint UNIQUE, title, company_name, location, remote, url, description,
  posted_at, deadline, first_seen_at, last_seen_at)
- job_enrichment (job_id PK FK ON DELETE CASCADE, seniority, category, skills text[],
  years_required, required_languages text[], match_score 0-100, match_reasons text[],
  model, enriched_at) — v1 CHECK constraints kept; `model` is the "enriched_by" field
  ('rule-based' or the Claude model name)
- etl_runs (v1 columns + source_id FK nullable + step CHECK IN ('extract','transform',
  'enrich')) — extract: one row per source (rows_extracted = fetched, rows_loaded = new
  raw postings); transform: one row per run, source_id NULL (rows_extracted = processed,
  rows_loaded = linked to a job; error_message notes how many postings failed);
  enrich: one row per run (rows_extracted = rows_loaded = jobs scored, rows_enriched =
  rows changed by rules/Claude, error_message = summary)
- schema_migrations (version, filename, checksum, applied_at) — managed by src/migrate.py

## Decisions
- Simple SQL migrations instead of Alembic/SQLAlchemy: numbered plain .sql files in
  migrations/, applied by src/migrate.py on pipeline startup, each in its own
  transaction (Postgres DDL is transactional). Applied files are never edited (checksum
  check); schema changes go into a new file. Docker no longer runs sql/init.sql.
- Raw data lives in the database (raw_postings JSONB), not in data/raw files, because a
  GitHub Actions run has no persistent disk. Re-processing reads from raw_postings.
- Raw upsert: ON CONFLICT DO UPDATE payload + last_seen_at; new vs already seen per row
  via RETURNING (xmax = 0). Duplicate external_ids within one fetch are dropped first.
- Extractors only fetch and return {external_id, payload}; they never clean data or touch
  the database. Mappers (src/mappers/, one per source name) turn a payload into a
  NormalizedJob. Registry + SOURCES env var in src/extractors/__init__.py.
- 1:n instead of a join table: one raw posting belongs to exactly one job, so the FK is
  raw_postings.job_id (the "many" side). job_sources was dropped in 002.
- Fingerprint = sha256("company|title|city"), each part normalized (src/normalize.py):
  fold = lowercase + Serbian Cyrillic -> Latin + no accents (đ -> dj); company: legal
  forms removed (doo/kft/zrt/gmbh/ltd... anywhere, short ones like ad/ag/inc only at the
  end); title: gender markers and punctuation removed (+ and # kept); city: aliases
  (Szabadka = Subotica, Újvidék = Novi Sad, Belgrade/Belgrád = Beograd...), postal codes
  removed, remote flag or "Remote/Távmunka/Rad od kuće" -> 'remote'. Changing a rule
  changes fingerprints (test_fingerprint_is_stable guards this).
- Transform: one transaction per raw posting; failures (mapping error, no title/company)
  get transform_error and are retried every run. Merge into an existing job: title and
  first_seen_at kept, COALESCE fills empty fields (never empties them), earliest
  posted_at; jobs.last_seen_at = max of its raw postings' last_seen_at (only forward).
- "New jobs" = jobs.first_seen_at >= start of the current pipeline run.
- Real manual CSVs (data/manual/*) are gitignored; the format is in data/manual/README.md,
  an example in data/examples/.
- pandas was removed with the v1 DataFrame transform (row-by-row processing isolates errors).
- Postgres is bound to 127.0.0.1 only; use POSTGRES_HOST=127.0.0.1, not localhost
  (Windows tries IPv6 ::1 first and waits 10 s).
- v2 work happens on the `v2` branch; `main` stays at v1 (tag v1.0).
- AI for extraction only: Claude (optional) extracts facts a posting states (seniority,
  category, skills, years_required, required_languages) via structured outputs; the
  rules (src/rules.py) extract the same fields for free. Without ANTHROPIC_API_KEY, or
  when a Claude call fails, the rule-based row stays (fallback); `model` records which.
- Enrich order per run: rules for every job without a Claude row (rule rows recomputed
  every run, so rule fixes apply to all jobs) -> score -> Claude for the ENRICH_LIMIT
  best-scoring rule-based jobs -> score again. ENRICH_LIMIT=0 = no Claude calls.
- Deterministic scoring with reasons (src/scoring.py, plain Python, never Claude):
  skills 40 (my levels, full points at 5 matched skills), seniority 25, city 20 (priority
  order, remote counts), role 15 (preferred role in title); any dealbreaker (title
  keyword, years_required >= min_years_required, a required language I lack) -> 0.
  Every point is explained in match_reasons ("+ PHP (3)", "- requires 3+ years: score 0").
- Scores are recomputed for all jobs every run (cheap), so a profile change applies at once.
- config/profile.yaml (my real profile) is gitignored; config/profile.example.yaml is
  committed and used, with a warning, when profile.yaml is missing. Docker mounts ./config
  read-only; PROFILE_PATH can point to another file. Parsed with yaml.safe_load (pyyaml).
- Rules fold text first (Cyrillic -> Latin, no accents) and know Hungarian/Serbian
  seniority words; years: "3+ years/év/godine", "min./legalább/najmanje 3", ranges (lower
  bound), >15 ignored; languages: German/French with a requirement cue in the same
  sentence and no "plus/would be/ideal" cue, or a description written in that language.

## Known limitations
- The same job posted with a Hungarian and a Serbian (or English) title is NOT matched
  ("Adatmérnök" vs "Data Engineer" vs "Inženjer podataka"): the fingerprint compares
  normalized text, it doesn't translate.
- Different city spellings outside the alias map, or a company written with/without an
  extra word ("Nordeus" vs "Nordeus Beograd"), give different fingerprints.
- Several real openings with the same company + title + city merge into one job.
- A raw posting is transformed once: if its payload changes later (e.g. new title), the
  job is not updated (only last_seen_at follows).
- Role keywords are matched literally: "php developer" does not match "PHP fejlesztő";
  add Hungarian/Serbian role words to preferred_roles if needed.
- Only an exact "Remote" location (or the source's remote flag) counts as remote:
  "UK - Remote" or "Remote (Germany)" get no city points.
- Rule-based language detection is keyword-based and can still misread a sentence.

## Phases (work on ONE phase at a time, never start the next one unasked)
0 Scan + this file
1 Refactor: common Extractor interface, new schema with SQL migrations,
  Arbeitnow + CSV extractors, tag current state as v1.0 first
2 Transform: normalization, fingerprint, dedup, runs log
3 Enrichment: rule-based + optional Claude, match score vs profile file
4 Gmail IMAP extractor
5 ATS extractor (companies.yaml)
6 New-postings report, tests, English README
7 Supabase + GitHub Actions scheduled run
8 Vercel React dashboard

## How to work with me
- I'm a beginner in Python, Docker and Postgres (background: PHP/MySQL, C#, React).
  I must be able to defend every decision in a job interview.
- Small steps. Explain each design decision briefly in comments and in your summary.
- Each source fails in isolation: one failing source must not stop the others.
- HTTP calls: timeout + retries with exponential backoff.
- Ask before adding new dependencies or making structural changes not in this plan.
- At the end of each task: list changed files, how to run/verify it, and what I should
  understand for an interview.

## Current status
Phase 3 done (branch v2): migration 003, profile (config/profile.yaml), enrich step
reconnected to jobs (rules for all jobs + optional Claude upgrades), Hungarian/Serbian
rules, years/language extraction, deterministic match score with reasons, top-10 new
jobs logged at the end of each run.
README.md still describes v1 (rewritten in Phase 6).
