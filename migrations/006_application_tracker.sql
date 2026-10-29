-- 006: application tracker (Phase 9.1, database only).
--
-- applications       = one row per company/position I apply (or plan to apply) to
-- application_events = what happened (sent, reply, follow-up...), newest last
-- A trigger turns each new event into the application's status and its NEXT
-- action (date + Hungarian reason), so the dashboard only inserts events and
-- the follow-up rules live in ONE place, not in the frontend.
--
-- Additive only: new tables, functions and views; nothing existing changes, so
-- the daily pipeline is unaffected (it never reads these tables).

-- "Today" in my time zone, not the server's (Supabase runs in UTC: between
-- 00:00 and 01:00/02:00 Budapest time, current_date would still be yesterday).
-- STABLE: same value within one statement; fine for defaults and views.
CREATE FUNCTION local_today() RETURNS DATE
LANGUAGE sql STABLE
SET search_path = ''
AS $$ SELECT (now() AT TIME ZONE 'Europe/Budapest')::date $$;


-- --- company_key: the SQL twin of src/normalize.py normalize_company() ---------
--
-- Why in SQL and not in Python: from 9.3 on, the dashboard and an Edge Function
-- insert applications, not the Python pipeline. As a GENERATED column the key
-- is always computed the same way, whoever inserts the row, and can't be set
-- wrong by a client. tests/test_tracker.py checks that SQL and Python agree.
--
-- fold(): Python uses Unicode NFKD to drop accents; plain Postgres has no
-- built-in for that (the unaccent extension would be an extra dependency), so
-- the letters used in Hungarian, Serbian and the other usual languages are
-- listed explicitly in translate().
--
-- IMMUTABLE (same input -> same output) is required for a generated column.
-- SET search_path = '': only built-in pg_catalog functions are used, and a
-- fixed search_path means nobody can shadow them with their own functions.
CREATE FUNCTION normalize_company_key(name TEXT) RETURNS TEXT
LANGUAGE plpgsql IMMUTABLE
SET search_path = ''
AS $$
DECLARE
    -- Same lists as LEGAL_FORMS_ANYWHERE / LEGAL_FORMS_AT_END in normalize.py.
    anywhere CONSTANT TEXT[] := ARRAY['doo', 'kft', 'zrt', 'nyrt', 'gmbh', 'mbh', 'ltd',
                                      'llc', 'plc', 'srl', 'sro', 'bv'];
    at_end   CONSTANT TEXT[] := ARRAY['ad', 'ag', 'as', 'bt', 'kkt', 'kg', 'co', 'corp', 'inc',
                                      'sa', 'se', 'spa', 'nv', 'oy', 'ab', 'ug', 'limited',
                                      'company'];
    t      TEXT := lower(coalesce(name, ''));
    tokens TEXT[];
    kept   TEXT[];
BEGIN
    -- fold: Cyrillic letters that become two Latin letters, special letters,
    -- then one-to-one: Cyrillic -> Latin and accented -> plain letter.
    t := replace(replace(replace(replace(t, 'љ', 'lj'), 'њ', 'nj'), 'џ', 'dz'), 'ђ', 'dj');
    t := replace(replace(replace(replace(t, 'đ', 'dj'), 'ß', 'ss'), 'æ', 'ae'), 'œ', 'oe');
    t := translate(t,
        'абвгдежзијклмнопрстћуфхцчш'
        || 'áàâäãåāăąçčćďéèêëěēęíìîïīľĺłňńñóòôöõőøōřŕšśşťţúùûüůűūýÿžźż',
        'abvgdezzijklmnoprstcufhccs'
        || 'aaaaaaaaacccdeeeeeeeiiiiilllnnnoooooooorrsssttuuuuuuuyyzzz');

    t := replace(t, '.', '');                                -- "d.o.o." -> "doo"
    t := regexp_replace(t, '[^[:alnum:]_[:space:]]', ' ', 'g');  -- "&", ",", "-" -> space
    t := regexp_replace(t, '^[[:space:]]+|[[:space:]]+$', '', 'g');
    IF t = '' THEN
        RETURN '';
    END IF;
    tokens := regexp_split_to_array(t, '[[:space:]]+');

    -- Drop legal forms anywhere (array order is kept by array_remove)...
    kept := tokens;
    FOR i IN 1 .. cardinality(anywhere) LOOP
        kept := array_remove(kept, anywhere[i]);
    END LOOP;
    -- ...and short ones only at the end: "erdsoft co kg" -> "erdsoft".
    WHILE cardinality(kept) > 1 AND kept[cardinality(kept)] = ANY (at_end) LOOP
        kept := kept[1 : cardinality(kept) - 1];
    END LOOP;
    -- A name that is ONLY a legal form ("Kft.") keeps it rather than becoming "".
    IF cardinality(kept) = 0 THEN
        kept := tokens;
    END IF;
    RETURN array_to_string(kept, ' ');
END
$$;


-- --- Tables ------------------------------------------------------------------------

CREATE TABLE applications (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_name       TEXT NOT NULL CHECK (btrim(company_name) <> ''),
    -- 'ErdSoft d.o.o.' and 'ErdSoft Kft.' -> 'erdsoft': lets the AI search
    -- (9.3) skip companies I already applied to, whatever the spelling.
    company_key        TEXT GENERATED ALWAYS AS (normalize_company_key(company_name)) STORED,
    position           TEXT,          -- NULL = open application (no specific posting)
    city               TEXT,
    language           TEXT CHECK (language IN ('hu', 'sr', 'en')),   -- of the letter; NULL = not decided yet
    cv_variant         TEXT CHECK (cv_variant IN ('hu', 'en')),       -- which CV I send
    -- Link to a pipeline job, if it came from job_feed. SET NULL: the
    -- application (my own data) survives if the job row is ever deleted.
    job_id             BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    posting_url        TEXT,
    deadline           DATE,
    applied_at         DATE,          -- set by the 'sent' event
    status             TEXT NOT NULL DEFAULT 'saved'
                       CHECK (status IN ('saved', 'sent', 'in_progress', 'rejected', 'no_response', 'offer')),
    next_action_date   DATE,          -- when I have to do something; NULL = nothing planned
    next_action_reason TEXT,          -- what, in Hungarian ("3 hete elküldve, írj emlékeztetőt")
    notes              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- FK columns are not indexed automatically in Postgres (unlike MySQL/InnoDB);
-- ON DELETE SET NULL on jobs would otherwise scan the whole table.
CREATE INDEX applications_job_id_idx ON applications (job_id);
CREATE INDEX applications_company_key_idx ON applications (company_key);

CREATE TABLE application_events (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- CASCADE: an event means nothing without its application.
    application_id BIGINT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    event_type     TEXT NOT NULL CHECK (event_type IN (
                       'saved', 'sent', 'forwarded', 'stated_date', 'reply', 'interview',
                       'follow_up_sent', 'rejection', 'offer', 'note')),
    occurred_at    DATE NOT NULL DEFAULT local_today(),
    body           TEXT,              -- e.g. the reply's text, or my note
    stated_date    DATE,              -- a date they gave me ("we'll answer by the 15th")
    -- A 'stated_date' event without the date would make no sense.
    CHECK (event_type <> 'stated_date' OR stated_date IS NOT NULL)
);
CREATE INDEX application_events_application_id_idx ON application_events (application_id);


-- --- Trigger: an event updates its application --------------------------------------
--
-- AFTER INSERT, FOR EACH ROW: runs once per new event, inside the same
-- transaction as the INSERT (if it fails, the event is not saved either).
-- Dates count from occurred_at (when it happened), not from when I typed it in.
--
-- SET search_path FROM CURRENT: the function remembers the schema it was
-- created in ("public" on Supabase, a temporary schema in the tests), so the
-- table names below always mean OUR tables.
CREATE FUNCTION apply_application_event() RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    app          applications%ROWTYPE;
    new_status   TEXT;
    next_date    DATE;
    next_reason  TEXT;
    follow_ups   INTEGER;
BEGIN
    IF NEW.event_type = 'note' THEN
        RETURN NULL;   -- a note changes nothing (return value is ignored for AFTER triggers)
    END IF;

    -- FOR UPDATE: lock the row, so two events arriving at once are applied
    -- one after the other, not on top of each other.
    SELECT * INTO app FROM applications WHERE id = NEW.application_id FOR UPDATE;
    new_status := app.status;

    CASE NEW.event_type
    WHEN 'saved' THEN
        new_status := 'saved';
        next_date := app.deadline - 2;          -- NULL if there is no deadline
        next_reason := '2 nap múlva lejár a határidő, küldd el a jelentkezést';
    WHEN 'sent' THEN
        new_status := 'sent';
        next_date := NEW.occurred_at + 21;
        next_reason := '3 hete elküldve, írj emlékeztetőt';
    WHEN 'forwarded' THEN
        new_status := 'in_progress';
        next_date := NEW.occurred_at + 14;
        next_reason := '2 hete továbbították, kérdezz rá';
    WHEN 'stated_date' THEN
        new_status := 'in_progress';
        next_date := NEW.stated_date;
        next_reason := 'Mára ígértek választ, nézd meg, jött-e';
    WHEN 'reply', 'interview' THEN
        new_status := 'in_progress';
        IF NEW.stated_date IS NOT NULL THEN
            next_date := NEW.stated_date;
            next_reason := 'Ma van a megbeszélt időpont';
        ELSE
            next_date := NEW.occurred_at + 14;
            next_reason := CASE NEW.event_type
                WHEN 'reply' THEN '2 hete válaszoltak, kérdezz rá'
                ELSE '2 hete volt az interjú, kérdezz rá' END;
        END IF;
    WHEN 'follow_up_sent' THEN
        -- Count my follow-ups since the last sign of life (sent, forwarded,
        -- a reply...): after a reply the counter starts again. "Since" =
        -- inserted later (higher id). The new event is already visible here.
        SELECT count(*) INTO follow_ups
        FROM application_events
        WHERE application_id = NEW.application_id
          AND event_type = 'follow_up_sent'
          AND id > coalesce((
              SELECT max(id) FROM application_events
              WHERE application_id = NEW.application_id
                AND event_type IN ('sent', 'forwarded', 'stated_date', 'reply', 'interview')
          ), 0);
        IF follow_ups >= 2 THEN
            new_status := 'no_response';      -- 2 reminders, no answer: give up
        ELSE
            next_date := NEW.occurred_at + 14;
            next_reason := '2 hete írtál emlékeztetőt, írj egy utolsót';
        END IF;
    WHEN 'rejection' THEN
        new_status := 'rejected';
    WHEN 'offer' THEN
        new_status := 'offer';
    END CASE;

    UPDATE applications
    SET status             = new_status,
        applied_at         = CASE WHEN NEW.event_type = 'sent' THEN NEW.occurred_at ELSE applied_at END,
        next_action_date   = next_date,
        -- no date -> no reason (e.g. 'saved' without a deadline)
        next_action_reason = CASE WHEN next_date IS NULL THEN NULL ELSE next_reason END
    WHERE id = NEW.application_id;
    RETURN NULL;
END
$$;

CREATE TRIGGER application_events_apply
AFTER INSERT ON application_events
FOR EACH ROW EXECUTE FUNCTION apply_application_event();

-- updated_at = time of the last change, whoever changed the row (trigger or dashboard).
CREATE FUNCTION set_updated_at() RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;   -- BEFORE trigger: the returned row is what gets written
END
$$;

CREATE TRIGGER applications_set_updated_at
BEFORE UPDATE ON applications
FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- --- Views for the dashboard (9.4) ------------------------------------------------------
-- security_invoker = true, like job_feed (005): RLS is checked as the caller.

-- "Ma teendő": active applications whose next action is due today or overdue.
CREATE VIEW todays_actions WITH (security_invoker = true) AS
SELECT
    a.id,
    a.company_name,
    a.position,
    a.city,
    a.status,
    a.next_action_date,
    a.next_action_reason,
    local_today() - a.next_action_date AS days_overdue,   -- 0 = due today
    a.posting_url
FROM applications AS a
WHERE a.status IN ('saved', 'sent', 'in_progress')
  AND a.next_action_date <= local_today()
ORDER BY a.next_action_date, a.id;

-- Saved but not sent yet, and the deadline has passed or is within 3 days.
CREATE VIEW expiring WITH (security_invoker = true) AS
SELECT
    a.id,
    a.company_name,
    a.position,
    a.city,
    a.deadline,
    a.deadline - local_today() AS days_left,   -- negative = already passed
    a.posting_url
FROM applications AS a
WHERE a.status = 'saved'
  AND a.deadline <= local_today() + 3
ORDER BY a.deadline, a.id;


-- --- Access: read-only for now (write policies come in 9.4) -------------------------------

ALTER TABLE applications       ENABLE ROW LEVEL SECURITY;
ALTER TABLE application_events ENABLE ROW LEVEL SECURITY;

-- Supabase grants ALL on new tables and views to anon and authenticated by
-- default: take it all away, then give back SELECT to authenticated only.
REVOKE ALL ON applications, application_events, todays_actions, expiring FROM anon, authenticated;
GRANT SELECT ON applications, application_events, todays_actions, expiring TO authenticated;

CREATE POLICY read_applications ON applications FOR SELECT TO authenticated USING (true);
CREATE POLICY read_application_events ON application_events FOR SELECT TO authenticated USING (true);
