-- 013: a search run is split into chunks (one per city, plus remote).
--
-- Run 9 timed out: the whole search was ONE long Claude request (run 8: 58 s
-- in a single request), which the time budget can't interrupt. Now the
-- dashboard calls the Edge Function once per chunk, one after another, each
-- with a small budget and its own 60 s limit, all under ONE search run:
--   chunks_total     how many chunks the run has (set when the run starts)
--   next_chunk       the index of the next chunk that may run (0, 1, 2...)
--   chunks_failed    chunks that ended with an error (their results: none)
--   last_activity_at when the last chunk started or finished
--
-- claim_search_chunk() lets each chunk run exactly once and in order: a
-- chunk call can't be repeated to get extra searches without the rate limit.
-- The hourly limit still counts runs (start_search_run), not chunks.
--
-- start_search_run() closes stuck runs by INACTIVITY now, not by age: a run
-- with 6 chunks may legitimately take ~6 minutes, but no chunk runs longer
-- than ~70 s. A stuck run that already stored results becomes 'success' (it
-- counts toward the limit, it cost searches and gave results); one without
-- results becomes 'error' (never counts, the 008 rule).
-- Additive: new columns with defaults, a new function, CREATE OR REPLACE
-- (keeps the grants from 007: EXECUTE for service_role only).

ALTER TABLE search_runs
    ADD COLUMN chunks_total     SMALLINT CHECK (chunks_total >= 0),   -- NULL = a run from before 013
    ADD COLUMN next_chunk       SMALLINT NOT NULL DEFAULT 0 CHECK (next_chunk >= 0),
    ADD COLUMN chunks_failed    SMALLINT NOT NULL DEFAULT 0 CHECK (chunks_failed >= 0),
    ADD COLUMN last_activity_at TIMESTAMPTZ;

CREATE OR REPLACE FUNCTION start_search_run(p_mode TEXT, p_params JSONB)
RETURNS TABLE (run_id BIGINT, next_allowed_at TIMESTAMPTZ)
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    last_start TIMESTAMPTZ;
BEGIN
    -- Transaction-level advisory lock: released automatically at COMMIT.
    PERFORM pg_advisory_xact_lock(hashtext('search_runs'));

    UPDATE search_runs AS r
    SET status = CASE WHEN EXISTS (SELECT 1 FROM search_results AS s WHERE s.run_id = r.id)
                      THEN 'success' ELSE 'error' END,
        error = concat_ws('; ', r.error,
                          'timeout: no activity for 5 minutes (the page was closed or the server stopped a chunk)'),
        finished_at = now()
    WHERE r.status = 'running'
      AND coalesce(r.last_activity_at, r.started_at) < now() - interval '5 minutes';

    SELECT max(started_at) INTO last_start
    FROM search_runs
    WHERE started_at > now() - interval '1 hour'
      AND status <> 'error';

    IF last_start IS NOT NULL THEN
        RETURN QUERY SELECT NULL::BIGINT, last_start + interval '1 hour';
        RETURN;
    END IF;

    RETURN QUERY
    INSERT INTO search_runs (mode, params, last_activity_at) VALUES (p_mode, p_params, now())
    RETURNING id, NULL::TIMESTAMPTZ;
END
$$;

-- Claim chunk p_chunk of a running run. Returns the run's mode and params
-- (the chunk is rebuilt from them, so the browser can't change what is
-- searched), or no row when the chunk may not run: wrong order, already run,
-- out of range, or the run is no longer running. One UPDATE = atomic: two
-- calls for the same chunk can't both succeed.
CREATE FUNCTION claim_search_chunk(p_run_id BIGINT, p_chunk INTEGER)
RETURNS TABLE (mode TEXT, params JSONB, chunks_total SMALLINT)
LANGUAGE sql
SET search_path FROM CURRENT
AS $$
    UPDATE search_runs AS r
    SET next_chunk = r.next_chunk + 1,
        last_activity_at = now()
    WHERE r.id = p_run_id
      AND r.status = 'running'
      AND r.next_chunk = p_chunk
      AND p_chunk < r.chunks_total
    RETURNING r.mode, r.params, r.chunks_total
$$;

REVOKE EXECUTE ON FUNCTION claim_search_chunk(BIGINT, INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION claim_search_chunk(BIGINT, INTEGER) TO service_role;
