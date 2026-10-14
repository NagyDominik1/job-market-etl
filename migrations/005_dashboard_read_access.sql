-- 005: read-only access for the dashboard (Phase 8).
--
-- The dashboard (React on Vercel) talks to Supabase's REST API with the public
-- "anon" key. Who is calling decides the database role:
--   anon          = not logged in      -> must see NOTHING
--   authenticated = logged in (me; sign-ups are disabled) -> may READ job_feed
-- The pipeline connects as the table owner and is not affected by any of this.
--
-- Two independent locks, both needed:
--   1. GRANTs (table privileges): may this role touch the table at all?
--   2. RLS policies (migration 004 enabled RLS): which ROWS may it see?

-- Supabase has these roles. A plain local Postgres doesn't, so create them
-- here (NOLOGIN: nobody can log in as them) to keep the migration working on
-- the local database and in the tests.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
END
$$;

-- The dashboard's single data source: one row per job, already joined.
--
-- security_invoker = true: the view runs with the permissions of the CALLER
-- (authenticated), so the RLS policies of jobs, job_enrichment, raw_postings
-- and sources are checked for that caller. Without it, a view runs with the
-- permissions of its OWNER (the pipeline user, who bypasses RLS), so anyone
-- allowed to read the view would see every row of every table behind it: the
-- view would silently bypass RLS.
CREATE VIEW job_feed WITH (security_invoker = true) AS
SELECT
    j.id,
    j.title,
    j.company_name,
    j.location,
    j.remote,
    j.url,
    j.deadline,
    j.first_seen_at,
    j.last_seen_at,
    e.match_score,
    e.match_reasons,
    e.seniority,
    e.skills,
    -- e.g. "arbeitnow, csv": every source this job was found in
    (
        SELECT string_agg(DISTINCT s.name, ', ' ORDER BY s.name)
        FROM raw_postings AS r
        JOIN sources AS s ON s.id = r.source_id
        WHERE r.job_id = j.id
    ) AS sources
FROM jobs AS j
LEFT JOIN job_enrichment AS e ON e.job_id = j.id;

-- 1. Privileges. Supabase grants ALL on new tables and views to anon and
-- authenticated by default, so first take everything away, then give back
-- exactly what the view needs: SELECT, nothing else (no INSERT/UPDATE/DELETE).
REVOKE ALL ON sources, raw_postings, jobs, job_enrichment, etl_runs, schema_migrations, job_feed
    FROM anon, authenticated;

-- security_invoker means the caller also needs SELECT on what the view reads.
-- raw_postings: only the three columns the view uses, NOT the raw payload.
GRANT SELECT ON job_feed, jobs, job_enrichment, sources TO authenticated;
GRANT SELECT (id, source_id, job_id) ON raw_postings TO authenticated;

-- The role must be allowed to look into the schema. (current_schema() is
-- "public" in production and a temporary schema in the tests.)
DO $$
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO authenticated', current_schema());
END
$$;

-- 2. Row Level Security policies: SELECT only, TO authenticated only.
-- USING (true) = every row; who may log in at all is controlled in Supabase
-- Auth (sign-ups disabled, only my user exists). anon gets no policy: 0 rows,
-- even if it ever got a GRANT again. etl_runs and schema_migrations get none.
CREATE POLICY read_jobs ON jobs FOR SELECT TO authenticated USING (true);
CREATE POLICY read_job_enrichment ON job_enrichment FOR SELECT TO authenticated USING (true);
CREATE POLICY read_raw_postings ON raw_postings FOR SELECT TO authenticated USING (true);
CREATE POLICY read_sources ON sources FOR SELECT TO authenticated USING (true);
