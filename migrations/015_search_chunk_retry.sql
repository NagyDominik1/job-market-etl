-- 015: "Újrapróbálás": a failed chunk once more, inside the same search run.
--
-- Run 10: the Subotica chunk timed out; the only way to get it was a new
-- search, which the hourly limit blocks (and which repeats every city).
-- Now the dashboard may retry ONE failed chunk of the run:
--   failed_chunks   indexes of the chunks that failed (the Edge Function adds them)
--   retried_chunks  indexes already retried: each chunk gets one retry
--
-- claim_search_chunk_retry() allows a retry only when
--   - the chunk failed and was not retried yet (bounded cost: at most one
--     extra chunk per failed chunk, never a whole new search),
--   - the run is finished (the chunk loop is over, so no two chunks of one
--     run record their counts at the same time),
--   - it is the LATEST run and started within the hour: an old run can't be
--     reopened to search more than once an hour.
-- It reopens the run ('running', as start_search_run expects for a run in
-- progress); the Edge Function closes it again after the retry.
-- One UPDATE = atomic: two clicks can't both claim the same retry.
-- Additive: new columns with defaults, a new function.

ALTER TABLE search_runs
    ADD COLUMN failed_chunks  SMALLINT[] NOT NULL DEFAULT '{}',
    ADD COLUMN retried_chunks SMALLINT[] NOT NULL DEFAULT '{}';

CREATE FUNCTION claim_search_chunk_retry(p_run_id BIGINT, p_chunk INTEGER)
RETURNS TABLE (mode TEXT, params JSONB, chunks_total SMALLINT)
LANGUAGE sql
SET search_path FROM CURRENT
AS $$
    UPDATE search_runs AS r
    SET failed_chunks = array_remove(r.failed_chunks, p_chunk::SMALLINT),
        retried_chunks = r.retried_chunks || p_chunk::SMALLINT,
        status = 'running',
        finished_at = NULL,
        last_activity_at = now()
    WHERE r.id = p_run_id
      AND r.status <> 'running'
      AND p_chunk = ANY (r.failed_chunks)
      AND NOT p_chunk = ANY (r.retried_chunks)
      AND r.started_at > now() - interval '1 hour'
      AND r.id = (SELECT max(id) FROM search_runs)
    RETURNING r.mode, r.params, r.chunks_total
$$;

REVOKE EXECUTE ON FUNCTION claim_search_chunk_retry(BIGINT, INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_search_chunk_retry(BIGINT, INTEGER) TO service_role;
