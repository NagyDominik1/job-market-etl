-- Database schema for job-market-etl.
-- This file runs automatically ONCE, when the Postgres container starts
-- with an empty data volume (see docker-compose.yml).
--
-- IMPORTANT: editing this file does NOT change an existing database.
-- After changing it, reset the database so it is created again:
--     docker compose down -v      (deletes ALL data in the database)
--     docker compose up --build   (recreates the tables, the pipeline reloads the jobs)

-- jobs: one row per job posting from the Arbeitnow API.
-- The API's "slug" is a unique ID per posting, so we use it as the primary key.
-- This lets the ETL "upsert": insert new jobs, update ones we've already seen.
-- first_seen_at / last_seen_at record when our ETL first and most recently saw the job.
CREATE TABLE IF NOT EXISTS jobs (
    slug          TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    company_name  TEXT,
    location      TEXT,
    remote        BOOLEAN,
    tags          TEXT[],        -- array of strings, e.g. {'Python','SQL'}
    job_types     TEXT[],        -- e.g. {'full time','internship'}
    url           TEXT,
    description   TEXT,
    posted_at     TIMESTAMPTZ,   -- when the job was posted (from the API)
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- etl_runs: a log of every ETL pipeline run.
-- Each run inserts a row with status 'running', then updates it to
-- 'success' or 'failed' at the end. Useful for monitoring and debugging.
CREATE TABLE IF NOT EXISTS etl_runs (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    status         TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    rows_extracted INTEGER,
    rows_loaded    INTEGER,
    rows_enriched  INTEGER,      -- jobs classified by Claude in this run (NULL if the step crashed)
    error_message  TEXT
);

-- job_enrichment: extra fields for a job, produced by the Claude API
-- (see src/enrich.py): seniority level, job category and required skills.
-- One row per job; a job without a row here has not been enriched yet.
-- ON DELETE CASCADE: if a job is deleted from `jobs`, its enrichment is deleted too.
-- The CHECK constraints guarantee only known values end up in the table, even
-- if the model (or a bug in our code) produced something else.
CREATE TABLE IF NOT EXISTS job_enrichment (
    slug        TEXT PRIMARY KEY REFERENCES jobs(slug) ON DELETE CASCADE,
    seniority   TEXT NOT NULL CHECK (seniority IN ('intern', 'junior', 'mid', 'senior', 'lead', 'unknown')),
    category    TEXT NOT NULL CHECK (category IN (
                    'data', 'backend', 'frontend', 'fullstack', 'devops', 'mobile',
                    'security', 'ai_ml', 'qa', 'design', 'non_tech', 'other')),
    skills      TEXT[] NOT NULL DEFAULT '{}',
    model       TEXT NOT NULL,   -- which Claude model produced this row
    enriched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
