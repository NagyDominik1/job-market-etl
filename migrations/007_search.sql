-- 007: AI search on the dashboard (Phase 9.3), database part.
--
-- search_profile = what the search looks for (the same structure as
--                  config/profile.yaml; one row, filled by me in Supabase)
-- search_runs    = one row per search: parameters, status, and the COST
--                  (web searches used, tokens), so every cent is traceable
-- search_results = what a run found (job postings or companies)
--
-- Who writes: only the Edge Function "search", with the service_role key
-- (server-side, never in the browser). The dashboard (authenticated) can only
-- READ. Additive only: nothing existing changes.

-- Supabase has service_role; a plain local Postgres doesn't. Create it here
-- (NOLOGIN: nobody can log in as it) so the migration and the tests work
-- locally, like 005 does for anon/authenticated. BYPASSRLS like on Supabase.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN BYPASSRLS;
    END IF;
END
$$;


-- --- search_profile: one row -----------------------------------------------------------
--
-- "id BOOLEAN PRIMARY KEY DEFAULT true CHECK (id)": the only possible key is
-- true, so a second row is impossible (a common single-row-table trick).
-- JSONB for the parts that are maps in profile.yaml (skill -> level,
-- language -> level, dealbreakers), arrays for the ordered lists.
-- My real values are NOT in git: sql/search_profile_template.sql is a template.
CREATE TABLE search_profile (
    id              BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    skills          JSONB  NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(skills) = 'object'),        -- {"PHP": 3, "SQL": 2}
    seniority       TEXT[] NOT NULL DEFAULT '{}',   -- {junior, mid}
    cities          TEXT[] NOT NULL DEFAULT '{}',   -- priority order; 'remote' = remote jobs
    languages       JSONB  NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(languages) = 'object'),     -- {"hungarian": "native"}
    preferred_roles TEXT[] NOT NULL DEFAULT '{}',
    dealbreakers    JSONB  NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(dealbreakers) = 'object'),  -- same keys as profile.yaml
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- --- search_runs: one row per search, with its cost -----------------------------------------

CREATE TABLE search_runs (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mode          TEXT NOT NULL CHECK (mode IN ('jobs', 'companies')),
    params        JSONB NOT NULL DEFAULT '{}',   -- the validated form input
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'success', 'error')),
    searches_used INTEGER NOT NULL DEFAULT 0,    -- web searches billed ($10 / 1000)
    input_tokens  INTEGER NOT NULL DEFAULT 0,    -- all input incl. cache reads/writes
    output_tokens INTEGER NOT NULL DEFAULT 0,
    error         TEXT                           -- short message, never a secret
);
CREATE INDEX search_runs_started_at_idx ON search_runs (started_at);


-- --- search_results -----------------------------------------------------------------------

CREATE TABLE search_results (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('job', 'company')),
    title           TEXT,               -- the position (jobs); NULL for companies
    company_name    TEXT NOT NULL CHECK (btrim(company_name) <> ''),
    -- Same generated key as applications.company_key (006), so "did I apply
    -- there already?" is a simple equality, whatever the spelling.
    company_key     TEXT GENERATED ALWAYS AS (normalize_company_key(company_name)) STORED,
    city            TEXT,
    remote          BOOLEAN,
    -- Links come from web pages, i.e. untrusted input: only http(s), so a
    -- "javascript:..." link can never reach the dashboard. The Edge Function
    -- checks this too; the CHECK is the last line of defence.
    url             TEXT CHECK (url ~* '^https?://'),
    deadline        DATE,
    contact         TEXT,               -- companies: careers page or e-mail
    reasons         TEXT[] NOT NULL DEFAULT '{}',
    score           SMALLINT CHECK (score BETWEEN 0 AND 100),
    already_applied BOOLEAN NOT NULL DEFAULT false,   -- set by the trigger below
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX search_results_run_id_idx ON search_results (run_id);


-- already_applied: decided by the DATABASE when the row is inserted, from
-- applications.company_key. Whoever inserts results gets it right, and the
-- rule is tested in SQL (tests/test_search_db.py). It is a snapshot: applying
-- later doesn't change old results (the dashboard can compare again in 9.4).
CREATE FUNCTION mark_already_applied() RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
BEGIN
    -- BEFORE INSERT: generated columns are not computed yet, so compute the key here.
    NEW.already_applied := EXISTS (
        SELECT 1 FROM applications
        WHERE company_key = normalize_company_key(NEW.company_name)
    );
    RETURN NEW;
END
$$;

CREATE TRIGGER search_results_mark_already_applied
BEFORE INSERT ON search_results
FOR EACH ROW EXECUTE FUNCTION mark_already_applied();


-- --- Rate limit: at most one search per hour --------------------------------------------------
--
-- Why in the database and not in the Edge Function's code: two clicks at the
-- same moment would both see "no run in the last hour" and both start. Here
-- the check and the INSERT happen in ONE transaction, behind a lock, so the
-- second call waits for the first and then sees its row.
--
-- Returns the new run's id, or NULL + the time the next search is allowed.
-- A run that failed without spending anything (input_tokens = 0, e.g. a bug
-- before the Claude call) doesn't count, so it can be retried at once.
CREATE FUNCTION start_search_run(p_mode TEXT, p_params JSONB)
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
      AND NOT (status = 'error' AND input_tokens = 0);

    IF last_start IS NOT NULL THEN
        RETURN QUERY SELECT NULL::BIGINT, last_start + interval '1 hour';
        RETURN;
    END IF;

    RETURN QUERY
    INSERT INTO search_runs (mode, params) VALUES (p_mode, p_params)
    RETURNING id, NULL::TIMESTAMPTZ;
END
$$;


-- --- Access ------------------------------------------------------------------------------------

ALTER TABLE search_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE search_runs    ENABLE ROW LEVEL SECURITY;
ALTER TABLE search_results ENABLE ROW LEVEL SECURITY;

-- Supabase grants ALL on new tables to anon and authenticated by default:
-- take it away, then give back SELECT to authenticated only.
REVOKE ALL ON search_profile, search_runs, search_results FROM anon, authenticated;
GRANT SELECT ON search_profile, search_runs, search_results TO authenticated;

CREATE POLICY read_search_profile ON search_profile FOR SELECT TO authenticated USING (true);
CREATE POLICY read_search_runs    ON search_runs    FOR SELECT TO authenticated USING (true);
CREATE POLICY read_search_results ON search_results FOR SELECT TO authenticated USING (true);

-- The Edge Function (service_role, bypasses RLS) needs these privileges.
-- Explicit, instead of relying on Supabase's default privileges.
GRANT SELECT ON search_profile, applications TO service_role;
GRANT SELECT, INSERT, UPDATE ON search_runs, search_results TO service_role;

-- Functions are executable by EVERYONE (PUBLIC) by default in Postgres, and
-- PostgREST exposes them as /rest/v1/rpc/...: without this, the logged-in
-- dashboard could create runs itself. Only the Edge Function may.
REVOKE EXECUTE ON FUNCTION start_search_run(TEXT, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION start_search_run(TEXT, JSONB) TO service_role;
