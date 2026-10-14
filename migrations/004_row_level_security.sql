-- 004: Row Level Security (RLS) on every table, with NO policies yet.
--
-- Why: Supabase automatically publishes the tables of the "public" schema
-- through its REST API (PostgREST), using the "anon" and "authenticated"
-- roles. With RLS enabled and no policy, those roles see zero rows and can't
-- write anything: the tables are closed by default.
--
-- The pipeline is not affected: it connects as the tables' OWNER, and
-- Postgres skips RLS for the owner (we don't use FORCE ROW LEVEL SECURITY).
-- Superusers, like the local Docker user, bypass RLS too.
--
-- Phase 8 (dashboard) will add read-only SELECT policies where needed.
-- Plain Postgres syntax, so it works the same on the local database.
ALTER TABLE sources           ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_postings      ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs              ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_enrichment    ENABLE ROW LEVEL SECURITY;
ALTER TABLE etl_runs          ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;
