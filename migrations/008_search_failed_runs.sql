-- 008: a failed search run never counts toward the 1-per-hour limit.
--
-- 007 skipped only failed runs that cost nothing (input_tokens = 0). Now every
-- run with status 'error' is skipped, so a bug or an API error can be retried
-- at once. Trade-off: a run that fails after spending tokens can also be
-- retried at once; the monthly spend limit on the Anthropic key is the cap.
-- 'running' runs still count, so two searches can't run at the same time.
--
-- CREATE OR REPLACE keeps the grants from 007 (EXECUTE for service_role only).
-- 007 is not edited (checksum check): schema changes go into a new file.

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
