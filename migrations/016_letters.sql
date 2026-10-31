-- 016: short application letters (Phase 9.5), database part.
--
-- letter_templates = my letter templates: one per language (hu/sr/en) and
--                    kind (posting = answer to a posting, open = open
--                    application). Placeholders, filled by the Edge Function
--                    "draft-letter": {company} {position} {tailored_line}.
-- letter_drafts    = one row per drafted letter: which language/CV was picked
--                    and the COST (tokens), like search_runs for the search.
--                    The letter text itself is not stored: I copy it and send
--                    it myself, never the system.
--
-- Who writes: the dashboard (authenticated) edits the templates ("Sablonok");
-- only the Edge Function (service_role) writes letter_drafts. My real
-- templates are NOT in git: sql/letter_templates_template.sql is a template,
-- the filled-in sql/letter_templates_local.sql is gitignored (like
-- search_profile, 007). Additive only: nothing existing changes.

CREATE TABLE letter_templates (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    language   TEXT NOT NULL CHECK (language IN ('hu', 'sr', 'en')),
    kind       TEXT NOT NULL CHECK (kind IN ('posting', 'open')),
    subject    TEXT NOT NULL CHECK (btrim(subject) <> '' AND length(subject) <= 200),
    body       TEXT NOT NULL CHECK (btrim(body) <> '' AND length(body) <= 2000),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (language, kind)      -- one template per combination: the function never has to choose
);

-- Same trigger function as applications (006).
CREATE TRIGGER letter_templates_set_updated_at
BEFORE UPDATE ON letter_templates
FOR EACH ROW EXECUTE FUNCTION set_updated_at();


CREATE TABLE letter_drafts (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- What the letter was for: an application or a search result (one of them).
    application_id   BIGINT REFERENCES applications(id) ON DELETE SET NULL,
    search_result_id BIGINT REFERENCES search_results(id) ON DELETE SET NULL,
    language         TEXT CHECK (language IN ('hu', 'sr', 'en')),
    kind             TEXT CHECK (kind IN ('posting', 'open')),
    cv_variant       TEXT CHECK (cv_variant IN ('hu', 'en')),
    status           TEXT NOT NULL CHECK (status IN ('success', 'error')),
    input_tokens     INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens    INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    error            TEXT,           -- short message, never a secret
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX letter_drafts_created_at_idx ON letter_drafts (created_at);


-- --- Access ------------------------------------------------------------------------------------

ALTER TABLE letter_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE letter_drafts    ENABLE ROW LEVEL SECURITY;

-- Supabase grants ALL on new tables to anon and authenticated by default.
REVOKE ALL ON letter_templates, letter_drafts FROM anon, authenticated;

-- Templates: read, add, edit the text. Column-level like 011: language and
-- kind are fixed once a row exists (an edit can't create a second "hu/posting"
-- by renaming another row); no DELETE (an empty template is refused anyway).
GRANT SELECT ON letter_templates TO authenticated;
GRANT INSERT (language, kind, subject, body) ON letter_templates TO authenticated;
GRANT UPDATE (subject, body) ON letter_templates TO authenticated;
CREATE POLICY read_letter_templates   ON letter_templates FOR SELECT TO authenticated USING (true);
CREATE POLICY insert_letter_templates ON letter_templates FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY update_letter_templates ON letter_templates FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

-- The cost log: read-only for the dashboard.
GRANT SELECT ON letter_drafts TO authenticated;
CREATE POLICY read_letter_drafts ON letter_drafts FOR SELECT TO authenticated USING (true);

-- The Edge Function (service_role, bypasses RLS): what it reads to draft a
-- letter, and the cost log it writes. Explicit, like 007.
GRANT SELECT ON letter_templates, applications, jobs, job_enrichment, search_results, search_profile TO service_role;
GRANT SELECT, INSERT ON letter_drafts TO service_role;
