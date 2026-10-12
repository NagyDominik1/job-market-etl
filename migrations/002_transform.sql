-- 002: what the Phase 2 transform step needs.
-- (001 is never edited once applied; every schema change gets a new file.)

-- raw_postings:
--   last_seen_at    when an extractor last delivered this posting. Together with
--                   fetched_at (first delivery) it shows how long a posting is online.
--   job_id          the deduplicated job this raw posting belongs to (NULL = not
--                   transformed yet). One raw posting belongs to exactly ONE job,
--                   one job can have MANY raw postings: a 1:n relationship, so the
--                   foreign key lives on the "many" side and no link table is needed.
--                   ON DELETE SET NULL: deleting a job keeps the raw data; the
--                   posting is simply transformed again on the next run.
--   transform_error why the transform step could not turn this posting into a job.
ALTER TABLE raw_postings
    ADD COLUMN last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN job_id          BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    ADD COLUMN transform_error TEXT;

-- Existing rows: we only know when they were first fetched.
UPDATE raw_postings SET last_seen_at = fetched_at;

-- Postgres does NOT index foreign key columns automatically. This index makes
-- "all raw postings of job X" and the ON DELETE SET NULL check fast.
CREATE INDEX raw_postings_job_id_idx ON raw_postings (job_id);

-- Replaced by raw_postings.job_id (see above). It was never filled.
DROP TABLE job_sources;

-- etl_runs.step: which part of the pipeline a run row belongs to.
-- Added in three steps because existing rows need a value before NOT NULL works:
-- add the column, fill it for the old rows (all were extract runs), then constrain it.
ALTER TABLE etl_runs ADD COLUMN step TEXT;
UPDATE etl_runs SET step = 'extract';
ALTER TABLE etl_runs
    ALTER COLUMN step SET NOT NULL,
    ADD CONSTRAINT etl_runs_step_check CHECK (step IN ('extract', 'transform', 'enrich'));
