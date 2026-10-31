-- 017: letters tailored to the posting.
--
-- requirements     3-5 key requirements of the posting, short phrases
--                  ("PHP 8", "REST API design", "English B2")
-- company_summary  one short sentence about the company, from the opened page
--
-- search_results: filled by the search for postings Claude opened (empty for
--   postings only seen on a list page: nothing was read about them).
-- applications: copied from the search result by "Hozzáadom", or filled by
--   draft-letter, which opens the posting_url once when they are missing.
--   NULL = not looked up yet; '{}' = looked up, nothing found (no second fetch).
-- search_profile.projects: my real projects, the only experience a letter may
--   mention ([{"name", "kind": "project" | "internship", "skills": [...]}]).
--   My real values are not in git (sql/search_profile_template.sql has placeholders).
--
-- Additive: new columns only (NULL or a default), new column-level grants.

ALTER TABLE search_results
    ADD COLUMN requirements    TEXT[] NOT NULL DEFAULT '{}' CHECK (cardinality(requirements) <= 5),
    ADD COLUMN company_summary TEXT CHECK (length(company_summary) <= 300);

ALTER TABLE applications
    ADD COLUMN requirements    TEXT[] CHECK (cardinality(requirements) <= 5),
    ADD COLUMN company_summary TEXT CHECK (length(company_summary) <= 300);

ALTER TABLE search_profile
    ADD COLUMN projects JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(projects) = 'array');

-- "Hozzáadom" inserts them with the application (011 grants INSERT column by
-- column; status etc. stay out). No UPDATE for the dashboard: they come from
-- the posting, not from typing.
GRANT INSERT (requirements, company_summary) ON applications TO authenticated;

-- draft-letter saves what it extracted, so the posting is opened only once.
GRANT UPDATE (requirements, company_summary) ON applications TO service_role;
