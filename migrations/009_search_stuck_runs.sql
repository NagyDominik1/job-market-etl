-- 009: search runs stuck in 'running' become 'error' (timeout).
--
-- When Supabase kills the Edge Function (150 s wall-clock limit), the function
-- can't mark its run as finished, so the row stays 'running' forever - and a
-- 'running' row counts toward the 1-per-hour limit (run 4 did exactly that).
-- No run lives longer than ~100 s, so 'running' after 5 minutes means dead.
--
-- start_search_run() now closes such runs first (status 'error', a timeout
-- message, finished_at), then applies the limit, where 'error' runs never
-- count (the 008 rule). The UPDATE below closes the ones that exist now.
-- CREATE OR REPLACE keeps the grants from 007 (EXECUTE for service_role only).

CREATE OR REPLACE FUNCTION start_search_run(p_mode TEXT, p_params JSONB)
RETURNS TABLE (run_id BIGINT, next_allowed_at TIMESTAMPTZ)
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    last_start TIMESTAMPTZ;
BEGIN
    -- Transaction-level advisory lock: released automatically at COMMIT.
    -- Any constant works; this one is just "search_runs" hashed.
    PERFORM pg_advisory_xact_lock(hashtext('search_runs'));

    UPDATE search_runs
    SET status = 'error',
        error = 'timeout: the search was stopped by the server (no result after 5 minutes)',
        finished_at = now()
    WHERE status = 'running'
      AND started_at < now() - interval '5 minutes';

    SELECT max(started_at) INTO last_start
    FROM search_runs
    WHERE started_at > now() - interval '1 hour'
      AND status <> 'error';

    IF last_start IS NOT NULL THEN
        RETURN QUERY SELECT NULL::BIGINT, last_start + interval '1 hour';
        RETURN;
    END IF;

    RETURN QUERY
    INSERT INTO search_runs (mode, params) VALUES (p_mode, p_params)
    RETURNING id, NULL::TIMESTAMPTZ;
END
$$;

-- Runs that are stuck right now (e.g. run 4 on Supabase).
UPDATE search_runs
SET status = 'error',
    error = 'timeout: the search was stopped by the server (no result after 5 minutes)',
    finished_at = now()
WHERE status = 'running'
  AND started_at < now() - interval '5 minutes';
