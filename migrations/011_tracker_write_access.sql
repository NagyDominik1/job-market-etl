-- 011: the dashboard may write the tracker (Phase 9.4, "Jelentkezéseim" tab).
--
-- 006 gave authenticated SELECT only. Now:
--   applications       INSERT + UPDATE, but only the columns I type in myself.
--                      status, applied_at and next_action_* stay trigger-only:
--                      they change through events, so the history always
--                      explains the current status.
--   application_events INSERT only. The event log is append-only: no UPDATE
--                      (the trigger runs AFTER INSERT, so an edited event would
--                      leave the status out of sync with the history) and no
--                      DELETE. A mistake is fixed with a new event or a note.
--   DELETE             on neither table.
-- anon still gets nothing. Additive only: grants, policies, one ALTER FUNCTION
-- and one NOT VALID constraint; no existing file changes.

-- 1. Column-level privileges. A table-level "GRANT UPDATE" would also let the
--    dashboard set status = 'offer' directly; listing the columns makes the
--    database refuse that (permission denied), not just the frontend.
GRANT INSERT (company_name, position, city, language, cv_variant, job_id, posting_url, deadline, notes)
    ON applications TO authenticated;
GRANT UPDATE (company_name, position, city, language, cv_variant, job_id, posting_url, deadline, notes)
    ON applications TO authenticated;
GRANT INSERT ON application_events TO authenticated;

-- 2. RLS policies (RLS is on since 006). USING/WITH CHECK (true), like the
--    read policies: there is only one user and sign-ups are disabled.
CREATE POLICY insert_applications ON applications
    FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY update_applications ON applications
    FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY insert_application_events ON application_events
    FOR INSERT TO authenticated WITH CHECK (true);

-- 3. The event trigger runs as the function's owner (SECURITY DEFINER).
--    A trigger normally runs as the user who inserted the event; then
--    authenticated would need UPDATE on status and next_action_*, which
--    point 1 deliberately withholds. Safe because: search_path is pinned
--    (SET search_path FROM CURRENT in 006), and a trigger function cannot be
--    called directly ("trigger functions can only be called as triggers"),
--    so it only ever runs on an event insert that RLS already allowed.
ALTER FUNCTION apply_application_event() SECURITY DEFINER;

-- 4. Links typed into the dashboard are shown as <a href>: only http(s), so a
--    "javascript:..." link can't be stored (same rule as search_results.url).
--    NOT VALID: checked for new and changed rows, existing rows are not
--    re-checked (so this migration can't fail on old data).
ALTER TABLE applications
    ADD CONSTRAINT applications_posting_url_http CHECK (posting_url ~* '^https?://') NOT VALID;

-- 5. The dashboard's duplicate warning computes the company_key of a name it
--    is about to add (supabase.rpc). The function is pure; Postgres grants
--    EXECUTE to PUBLIC by default, this states the intent explicitly.
GRANT EXECUTE ON FUNCTION normalize_company_key(TEXT) TO authenticated;
