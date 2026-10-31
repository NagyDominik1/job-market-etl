-- 014: "Már jelentkeztél" on every Job feed card, not only after Hozzáadom.
--
-- The dashboard needs to know which pipeline jobs are at a company I already
-- applied to. The company rules live in the database (normalize_company_key,
-- 006), so the comparison happens here too: no JS copy of the rules to drift.
-- A new view instead of a change to job_feed (additive; job_feed stays as is).
--
-- One row per job I applied to: same company_key (any spelling of the
-- company), or an application linked to the job itself (applications.job_id).
-- The dashboard asks only for the jobs it shows (WHERE job_id IN (...)), so
-- the function runs for at most 200 rows, and applications is small.
--
-- security_invoker: RLS and grants are checked as the caller (like job_feed,
-- 005); authenticated already reads jobs and applications and may execute
-- normalize_company_key (011). Search results need no view: search_results
-- has its own company_key column (007), compared in the browser.

CREATE VIEW job_applied WITH (security_invoker = true) AS
SELECT j.id AS job_id
FROM jobs AS j
WHERE EXISTS (
    SELECT 1 FROM applications AS a
    WHERE a.job_id = j.id
       OR a.company_key = normalize_company_key(j.company_name)
);

-- Supabase grants ALL on new views to anon/authenticated by default.
REVOKE ALL ON job_applied FROM anon, authenticated;
GRANT SELECT ON job_applied TO authenticated;
