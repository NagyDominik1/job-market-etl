-- Database schema for job-market-etl.
-- This file runs automatically ONCE, when the Postgres container starts
-- with an empty data volume (see docker-compose.yml).

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
    error_message  TEXT
);
