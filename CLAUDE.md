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

## Schema (migrations/001_initial_v2.sql)
- sources (id, name UNIQUE, kind CHECK IN ('email','ats','csv','api')) — one row per
  extractor, inserted by the pipeline itself
- raw_postings (id, source_id FK, external_id, payload JSONB, fetched_at,
  UNIQUE(source_id, external_id))
- jobs (id, fingerprint UNIQUE, title, company_name, location, remote, url, description,
  posted_at, deadline, first_seen_at, last_seen_at) — filled from Phase 2
- job_sources (job_id FK, raw_posting_id FK, PK both) — n:m link, filled from Phase 2
- job_enrichment (job_id PK FK ON DELETE CASCADE, seniority, category, skills text[],
  match_score 0-100, model, enriched_at) — v1 CHECK constraints kept; `model` is the
  "enriched_by" field ('rule-based' or the Claude model name); filled from Phase 3
- etl_runs (v1 columns + source_id FK nullable) — one row per source per run;
  rows_extracted = fetched, rows_loaded = new raw postings
- schema_migrations (version, filename, checksum, applied_at) — managed by src/migrate.py

## Decisions
- Simple SQL migrations instead of Alembic/SQLAlchemy: numbered plain .sql files in
  migrations/, applied by src/migrate.py on pipeline startup, each in its own
  transaction (Postgres DDL is transactional). Applied files are never edited (checksum
  check); schema changes go into a new file. Docker no longer runs sql/init.sql.
- Raw data lives in the database (raw_postings JSONB), not in data/raw files, because a
  GitHub Actions run has no persistent disk. Re-processing reads from raw_postings.
- Extractors only fetch and return {external_id, payload}; they never clean data or touch
  the database. Registry + SOURCES env var in src/extractors/__init__.py.
- CSV external_id = SHA-256 of the url, or of normalized title|company|city if no url.
- v2 work happens on the `v2` branch; `main` stays at v1 (tag v1.0).
- v2 starts with an empty database: run `docker compose down -v` once to drop the v1 volume.

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
Phase 1 done (branch v2): migrations, v2 schema, Extractor interface, Arbeitnow + CSV
extractors, multi-source runner writing raw_postings and etl_runs.
Not wired up yet: src/transform.py, src/load.py and src/enrich.py are v1 code (they still
use the v1 `slug`-based tables and data/raw files); Phase 2-3 adapt them to the new schema.
README.md still describes v1 (rewritten in Phase 6).
