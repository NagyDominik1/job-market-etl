-- 001: v2 schema (multi-source pipeline).
--
-- Applied by src/migrate.py, which runs every not-yet-applied file in this
-- folder in filename order, each in its own transaction, and records it in
-- schema_migrations. NEVER edit a migration after it was applied somewhere:
-- add a new file (002_..., 003_...) instead. The runner refuses to start if an
-- applied file was changed (it compares checksums).
--
-- Plain CREATE TABLE (no IF NOT EXISTS) on purpose: if a v1 database is still
-- in the volume, this fails loudly instead of silently keeping the old tables.
-- Fix: docker compose down -v (deletes the old data), then start again.

-- sources: where postings come from (one row per extractor, e.g. 'arbeitnow').
-- The pipeline inserts its extractors here itself, so no seed data is needed.
CREATE TABLE sources (
    id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('email', 'ats', 'csv', 'api'))
);

-- raw_postings: every posting exactly as the source delivered it (JSONB).
-- Kept in the database (not in files) because a scheduled GitHub Actions run
-- has no persistent disk. If a cleaning rule is wrong, we can re-process from here.
-- UNIQUE(source_id, external_id): the same posting from the same source is stored
-- once; "INSERT ... ON CONFLICT DO NOTHING" skips postings we have already seen.
CREATE TABLE raw_postings (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id   BIGINT NOT NULL REFERENCES sources(id),
    external_id TEXT NOT NULL,      -- the posting's ID at the source (or a hash we compute)
    payload     JSONB NOT NULL,     -- the raw posting, unchanged
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, external_id)
);

-- jobs: one row per REAL job, after cleaning and de-duplication (filled in Phase 2).
-- The same job can arrive from several sources; the fingerprint (a hash of the
-- normalized title + company + location) identifies it across sources.
CREATE TABLE jobs (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fingerprint   TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    company_name  TEXT,
    location      TEXT,
    remote        BOOLEAN,
    url           TEXT,
    description   TEXT,
    posted_at     TIMESTAMPTZ,      -- when the job was posted (from the source)
    deadline      DATE,             -- application deadline, if the source has one
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- job_sources: which raw postings a job was built from (many-to-many link table).
-- ON DELETE CASCADE: deleting a job or a raw posting also deletes its links.
CREATE TABLE job_sources (
    job_id         BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    raw_posting_id BIGINT NOT NULL REFERENCES raw_postings(id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, raw_posting_id)
);
-- The primary key index starts with job_id, so it can't speed up lookups by
-- raw_posting_id ("which job is this raw posting part of?"). This index can.
CREATE INDEX job_sources_raw_posting_id_idx ON job_sources (raw_posting_id);

-- job_enrichment: seniority, category, skills and match score per job (Phase 3).
-- One row per job; a job without a row here has not been enriched yet.
-- The CHECK constraints are the same as in v1: only known values get in, even
-- if the model (or a bug in our code) produced something else.
CREATE TABLE job_enrichment (
    job_id      BIGINT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    seniority   TEXT NOT NULL CHECK (seniority IN ('intern', 'junior', 'mid', 'senior', 'lead', 'unknown')),
    category    TEXT NOT NULL CHECK (category IN (
                    'data', 'backend', 'frontend', 'fullstack', 'devops', 'mobile',
                    'security', 'ai_ml', 'qa', 'design', 'non_tech', 'other')),
    skills      TEXT[] NOT NULL DEFAULT '{}',
    match_score SMALLINT CHECK (match_score BETWEEN 0 AND 100),  -- fit with my profile; NULL = not scored yet
    model       TEXT NOT NULL,   -- who produced this row: a Claude model name or 'rule-based'
    enriched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- etl_runs: a log of every pipeline run, as in v1, plus the source.
-- One row per source per run (source_id set), or one row for steps that work
-- on all sources at once (source_id NULL, e.g. the Phase 2 transform).
-- For an extract run: rows_extracted = postings fetched,
--                     rows_loaded    = NEW postings inserted into raw_postings,
-- so "already seen" = rows_extracted - rows_loaded.
CREATE TABLE etl_runs (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id      BIGINT REFERENCES sources(id),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    status         TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    rows_extracted INTEGER,
    rows_loaded    INTEGER,
    rows_enriched  INTEGER,
    error_message  TEXT
);
