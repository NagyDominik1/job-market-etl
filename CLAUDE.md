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
- applications (006; id, company_name, company_key GENERATED from normalize_company_key(),
  position NULL = open application, city, language hu/sr/en, cv_variant hu/en, job_id FK ->
  jobs ON DELETE SET NULL, posting_url, deadline, applied_at, status saved/sent/in_progress/
  rejected/no_response/offer, next_action_date, next_action_reason (Hungarian), notes,
  created_at, updated_at (BEFORE UPDATE trigger))
- application_events (006; id, application_id FK ON DELETE CASCADE, event_type, occurred_at
  DATE default local_today(), body, stated_date; stated_date required for 'stated_date')
- Views todays_actions, expiring (006, security_invoker)

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

- Managed Postgres (Supabase) in production: no server to run, backups and TLS included;
  the code only sees DATABASE_URL (src/db.py: it takes precedence over POSTGRES_*,
  sslmode=require unless the URL sets one, never logged). Locally nothing changed;
  `docker compose --profile supabase run --rm --build pipeline-supabase` runs the same
  image against Supabase with the gitignored .env.supabase.
- Session pooler (aws-1-eu-central-1.pooler.supabase.com:5432), not the direct
  connection (IPv6-only; Docker Desktop and GitHub runners have no IPv6) and not the
  transaction pooler (port 6543; no prepared statements, which psycopg uses
  automatically). Passwords in the URL must be URL-encoded.
- RLS on by default (migration 004): every table has Row Level Security enabled with no
  policies, so Supabase's REST API (anon/authenticated) sees nothing; the pipeline is the
  table owner and is unaffected. Phase 8 adds read-only SELECT policies.
- Same image in CI: .github/workflows/pipeline.yml builds our Dockerfile and runs it
  (daily 05:00 UTC + workflow_dispatch, concurrency group, 15 min timeout,
  SOURCES=arbeitnow). Secrets DATABASE_URL, PROFILE_YAML (written to
  config/profile.yaml on the runner and mounted read-only), optional ANTHROPIC_API_KEY;
  passed with `-e NAME` so they never appear on a command line. Exit code 1 -> red run.
- Tests never touch DATABASE_URL: DB fixtures skip when it is set.
- CI runner pinned to ubuntu-24.04 (not ubuntu-latest, which moves without warning).
- Dashboard (dashboard/, Vite + React, plain JS and CSS, supabase-js): reads only the
  job_feed view; all filters run in the database (PostgREST), max 200 rows shown.
  Login = magic link (signInWithOtp, shouldCreateUser: false) to the only existing user;
  sign-ups are disabled in Supabase. Config: VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY
  in dashboard/.env.local (gitignored) and in Vercel (Root Directory = dashboard). The
  anon/publishable key is public by design; the service_role key is never used there.
- Read access (migration 005): job_feed view WITH (security_invoker = true), so RLS is
  checked as the caller (a normal view runs as its owner and bypasses RLS). Two locks:
  REVOKE ALL from anon/authenticated (Supabase grants ALL by default), then GRANT SELECT
  to authenticated on job_feed, jobs, job_enrichment, sources and only (id, source_id,
  job_id) of raw_postings; plus SELECT-only policies TO authenticated. anon: nothing.
  The migration creates anon/authenticated locally when missing.
- Future migrations that add tables: enable RLS and REVOKE from anon/authenticated
  explicitly (Supabase's default privileges grant ALL on new tables).
- Tracker rules live in the database (006): an AFTER INSERT trigger on application_events
  sets the application's status + next action (saved: deadline-2; sent: +21 and applied_at;
  forwarded: +14; stated_date: that date; reply/interview: stated_date or +14;
  follow_up_sent: +14, the 2nd since the last sent/forwarded/stated_date/reply/interview
  (by id = insertion order) -> no_response; rejection/offer: final; note: nothing). Dates
  count from occurred_at. The dashboard only inserts events.
- company_key is a GENERATED column computed by normalize_company_key() (plpgsql, the SQL
  twin of normalize_company; accents via an explicit translate() list, no unaccent
  extension). test_company_key_matches_python_normalize_company keeps them equal: change
  both together.
- "Today" for the tracker = local_today() = Budapest date (Supabase runs in UTC).
- Functions: SET search_path = '' when only built-ins are used; SET search_path FROM
  CURRENT when they use our tables (works in "public" and in the test schemas).
- A database ahead of the code is safe: migrate.py ignores applied versions without a file
  (tests/test_migrate_ahead.py), so a branch's migration can be applied to
  Supabase before main has the file.
- Node/npm for the dashboard: versions pinned in package.json, package-lock.json
  committed; Node is not required for the pipeline.
- AI search confirms job results with Anthropic's web_fetch (single posting pages Claude
  found by search, max 8 per run, max 1 run per hour, robots.txt respected by the
  fetcher). Our code fetches nothing; this is on-demand reading of individual postings,
  not a crawler, so it stays within the "no scraping" rule. The code drops job results
  that aren't confirmed, link to a list/search page (isListingUrl, a heuristic), or hit a
  profile dealbreaker (seniority, min_years_required, title keywords) - the prompt asks
  for the same, the code enforces it.
- Search run time budget: Supabase kills an Edge Function at 150 s wall clock (run 4 was
  killed: 15 searches + 20 fetches, and a timeout retried by the SDK). Now max 8 searches +
  8 fetches, no SDK retries, the whole run within 95 s: after 55 s Claude is told to stop
  and submit what it has confirmed (25 s reserved for that), so confirmed results are
  saved instead of lost; the response says stopped_early. A killed run can't close its
  row, so start_search_run() (009) marks runs 'running' for > 5 min as 'error' (timeout);
  'error' runs never count toward the 1-per-hour limit (008).
- Search web tools are called directly (allowed_callers ["direct"]), not via dynamic
  filtering (code execution, the default since the _20260209 versions): run 5 searched 6
  times with dynamic filtering, never fetched and submitted nothing. Direct calls are the
  documented search -> fetch flow and every result/error comes back, so it can be counted.
  The function also rejects one submit_results made before any web_fetch (an error
  tool_result: "open the postings first").
- Per-run counts in search_runs (010): fetches_used, candidates, dropped_unconfirmed /
  senior / years / dealbreaker / bad_url / other, stopped_early. The Keresés tab shows them
  and explains a run with 0 results. The function saves the run without them if 010 is
  missing, so a run never stays 'running' because the code is ahead of the database.
- linkedin.com is never fetched (its terms forbid automated access): it is in web_fetch's
  blocked_domains (FETCH_BLOCKED_DOMAINS in validate.ts), so LinkedIn is used only through
  search results. A LinkedIn posting counts only if Claude finds and opens the same posting
  elsewhere; a job result that still links to linkedin.com is dropped as unconfirmed.

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
- The dashboard policies use USING (true): access control relies on only one user
  existing and sign-ups being disabled (no per-user rows; could be restricted to my
  email via auth.jwt() later, Supabase-only).
- Supabase's built-in email sender allows only a few magic-link emails per hour.

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
9 Job search assistant on the website (plan below)

## Phase 9: Job search assistant on the website
Goal: everything happens on the dashboard: AI search for job postings and for IT companies
(open applications), application tracking with follow-up reminders, short letter drafting.
The existing pipeline (extract/transform/enrich, daily GitHub Actions) must NOT change.

Steps (one at a time, each on its own branch, ask me before merging to main):
9.1 Tracker tables (database only).
9.2 I create an Anthropic API key with a monthly spend limit and store it myself as the
    Supabase Edge Function secret ANTHROPIC_API_KEY. Never in the frontend, never in a
    VITE_ variable, never in git.
9.3 Edge Function "search": two modes (jobs | companies). Parameters: countries, cities,
    remote, roles, seniority, languages. Calls Claude (claude-haiku-5-5) with the web search
    tool, max_uses 15, structured JSON output (title/company, city, url, deadline,
    contact for companies, reasons, score). Excludes companies I already applied to
    (company_key). Checks the logged-in user, max 1 search per hour. Stores results in
    search_results and a cost log in search_runs (searches used, tokens).
9.4 Dashboard tab "Jelentkezéseim": add from search results or job_feed, status buttons
    (sent / reply / forwarded / stated date / follow-up / rejection), "Ma teendő" list,
    expiring list. Needs INSERT/UPDATE policies for authenticated on tracker tables only.
9.5 Edge Function "draft-letter": letter_templates table (hu/sr/en, posting/open),
    4-5 sentences max, suggests the CV (hu or en), uses match reasons for one tailored line.

Rules: migrations are additive only; all existing tests must pass; secrets never printed.

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
Phases 7 and 8 merged into main; tag v2.0 = main before Phase 9 (6a45ceb).
Phase 9.1 merged into main (2026-10-29): migration 006 (applications,
application_events, trigger, todays_actions/expiring views, SELECT-only access), applied
to Supabase.
Phase 9.3 merged into main (2026-10-29): migration 007 (search_profile, search_runs,
search_results) applied to Supabase; Edge Function "search" deployed (verify_jwt = true);
Keresés tab on the dashboard. The search_profile row is filled from the gitignored
sql/search_profile_local.sql. First real search (run 1) failed with a bare "Claude API
error 400"; the function (version 2) now stores the API's error type + message + request
id in search_runs.error. Migration 008: failed runs (status error) never count toward the
1-per-hour limit (applied to Supabase by the next pipeline run).
Localhost magic-link login still redirects to Vercel (emailRedirectTo now ends in "/";
check the Supabase email template and Redirect URLs).
Next: 9.4 (Jelentkezéseim tab).
Open phases: 4 (Gmail IMAP), 5 (ATS), 6 (report).
