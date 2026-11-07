-- 018: one search per 5 minutes instead of one per hour; search_profile.pluses.
--
-- The hourly limit (007/008/013) was a cost cap from before the chunks: a run
-- is now at most 6 chunks x 4 searches, and the monthly spend limit on the
-- Anthropic key is the real cap. 5 minutes keeps a double click or a loop from
-- starting runs back to back, and lets me search again soon with other cities
-- or roles (the query plan rotates the adjacent role families per run).
--
-- New rule in start_search_run():
--   - a run that is still 'running' (with activity in the last 5 minutes)
--     blocks a new one, however long ago it started: a 6-chunk run can take
--     ~6 minutes, and two runs at once would mix their chunk counts and cost.
--     Before, the hour window covered this; 5 minutes alone would not.
--   - otherwise the last run that did not fail must have started 5+ minutes ago.
--   - failed runs ('error') still never count (008), stuck runs are still
--     closed first (013, same 5 minutes of inactivity).
-- next_allowed_at: 5 minutes after the last start, or (a run still running)
-- at least one minute from now: when it ends is unknown, the page may ask again.
--
-- claim_search_chunk_retry() (015) is unchanged: one retry per failed chunk,
-- only for the LATEST run, within its hour; a run reopened by a retry is
-- 'running', so it blocks a new search like any running run.
--
-- search_profile.pluses: things that are not skills or languages but help for
-- some roles (e.g. "EU citizen"); the search shows them in the reason of the
-- support/consultant bonus (score.ts). My real values are only in the
-- gitignored sql/search_profile_local.sql.
-- Additive: CREATE OR REPLACE (keeps the grants from 007: EXECUTE for
-- service_role only) and a new column with a default (the table-level SELECT
-- grants of 007 cover it).

CREATE OR REPLACE FUNCTION start_search_run(p_mode TEXT, p_params JSONB)
RETURNS TABLE (run_id BIGINT, next_allowed_at TIMESTAMPTZ)
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    last_start TIMESTAMPTZ;
    still_running BOOLEAN;
BEGIN
    -- Transaction-level advisory lock: released automatically at COMMIT.
    PERFORM pg_advisory_xact_lock(hashtext('search_runs'));

    -- Stuck runs first (013): no activity for 5 minutes.
    UPDATE search_runs AS r
    SET status = CASE WHEN EXISTS (SELECT 1 FROM search_results AS s WHERE s.run_id = r.id)
                      THEN 'success' ELSE 'error' END,
        error = concat_ws('; ', r.error,
                          'timeout: no activity for 5 minutes (the page was closed or the server stopped a chunk)'),
        finished_at = now()
    WHERE r.status = 'running'
      AND coalesce(r.last_activity_at, r.started_at) < now() - interval '5 minutes';

    SELECT EXISTS (SELECT 1 FROM search_runs WHERE status = 'running') INTO still_running;

    SELECT max(started_at) INTO last_start
    FROM search_runs
    WHERE started_at > now() - interval '5 minutes'
      AND status <> 'error';

    IF still_running OR last_start IS NOT NULL THEN
        RETURN QUERY SELECT NULL::BIGINT,
            greatest(last_start + interval '5 minutes',
                     CASE WHEN still_running THEN now() + interval '1 minute' END);
        RETURN;
    END IF;

    RETURN QUERY
    INSERT INTO search_runs (mode, params, last_activity_at) VALUES (p_mode, p_params, now())
    RETURNING id, NULL::TIMESTAMPTZ;
END
$$;

ALTER TABLE search_profile
    ADD COLUMN pluses TEXT[] NOT NULL DEFAULT '{}';   -- e.g. {"EU citizen"}
