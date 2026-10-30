-- 010: per-run counts for the AI search, so a run with 0 results explains itself.
--
-- Run 5 ended with 0 results and the table could not say why (it turned out
-- Claude submitted no candidates and never opened a page). Now every run
-- stores what happened between "Claude searched" and "results saved":
--   fetches_used        posting pages Claude opened (web_fetch)
--   candidates          results Claude submitted, before our checks
--   dropped_unconfirmed not opened with web_fetch (or on a blocked domain)
--   dropped_senior      senior/lead level (field or title) not in the profile
--   dropped_years       requires profile.min_years_required years or more
--   dropped_dealbreaker a dealbreaker title keyword
--   dropped_bad_url     no URL, or a search/list/careers-index page
--   dropped_other       invalid, duplicate or over the result limit
--   stopped_early       the time budget ended the run (results may be incomplete)
-- Plain integer columns instead of one JSONB: easy to read, sum and check.
-- Additive only; the grants from 007 are table-level, so they cover the new
-- columns (authenticated: SELECT, service_role: SELECT/INSERT/UPDATE).

ALTER TABLE search_runs
    ADD COLUMN fetches_used        INTEGER NOT NULL DEFAULT 0 CHECK (fetches_used >= 0),
    ADD COLUMN candidates          INTEGER          CHECK (candidates >= 0),  -- NULL = run never got an answer
    ADD COLUMN dropped_unconfirmed INTEGER NOT NULL DEFAULT 0 CHECK (dropped_unconfirmed >= 0),
    ADD COLUMN dropped_senior      INTEGER NOT NULL DEFAULT 0 CHECK (dropped_senior >= 0),
    ADD COLUMN dropped_years       INTEGER NOT NULL DEFAULT 0 CHECK (dropped_years >= 0),
    ADD COLUMN dropped_dealbreaker INTEGER NOT NULL DEFAULT 0 CHECK (dropped_dealbreaker >= 0),
    ADD COLUMN dropped_bad_url     INTEGER NOT NULL DEFAULT 0 CHECK (dropped_bad_url >= 0),
    ADD COLUMN dropped_other       INTEGER NOT NULL DEFAULT 0 CHECK (dropped_other >= 0),
    ADD COLUMN stopped_early       BOOLEAN NOT NULL DEFAULT false;
