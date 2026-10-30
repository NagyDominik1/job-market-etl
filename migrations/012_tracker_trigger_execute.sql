-- 012: nobody may call the event trigger function directly.
--
-- 011 made apply_application_event() SECURITY DEFINER. Supabase's security
-- advisor then flagged it as "executable by anon/authenticated via
-- /rest/v1/rpc". Postgres already refuses to call a trigger function outside
-- a trigger, but the intent should be visible in the grants too: Postgres
-- grants EXECUTE to PUBLIC by default, so take it away. The trigger keeps
-- working: EXECUTE is checked when a trigger is CREATED, not when it fires.

REVOKE EXECUTE ON FUNCTION apply_application_event() FROM PUBLIC, anon, authenticated;
