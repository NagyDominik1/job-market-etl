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

## Target schema
sources, raw_postings (UNIQUE(source_id, external_id)), jobs (title, company, city,
technologies text[], seniority, deadline, fingerprint UNIQUE, first_seen, match_score,
enriched_by), job_sources (n:m), runs (started_at, finished_at, status, new_count,
duplicate_count, error)

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
Phase 0 in progress.
