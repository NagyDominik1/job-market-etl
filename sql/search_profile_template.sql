-- Template for the single search_profile row (migration 007).
-- Copy it into Supabase -> SQL Editor, replace the <...> placeholders with
-- your own values (like in config/profile.yaml) and run it.
-- NEVER commit the filled-in version: this file stays a template.
--
-- Running it again updates the row (ON CONFLICT), so it also works for changes.

INSERT INTO search_profile (skills, seniority, cities, languages, preferred_roles, dealbreakers)
VALUES (
    -- skill -> level (1 = basics, 2 = used in a project, 3 = confident)
    '{"<SKILL_1>": 3, "<SKILL_2>": 2, "<SKILL_3>": 1}',
    -- any of: intern, junior, mid, senior, lead
    ARRAY['<SENIORITY_1>', '<SENIORITY_2>'],
    -- priority order (first = most wanted); 'remote' = remote jobs
    ARRAY['<CITY_1>', '<CITY_2>', 'remote'],
    -- language -> level, e.g. native, fluent, good, basic
    '{"<LANGUAGE_1>": "<LEVEL>", "<LANGUAGE_2>": "<LEVEL>"}',
    -- words looked for in job titles
    ARRAY['<ROLE_1>', '<ROLE_2>'],
    -- same keys as dealbreakers in config/profile.yaml
    '{"title_keywords": ["<WORD_1>", "<WORD_2>"],
      "min_years_required": <NUMBER>,
      "required_languages_missing": ["<LANGUAGE_I_DONT_SPEAK>"]}'
)
ON CONFLICT (id) DO UPDATE SET
    skills          = EXCLUDED.skills,
    seniority       = EXCLUDED.seniority,
    cities          = EXCLUDED.cities,
    languages       = EXCLUDED.languages,
    preferred_roles = EXCLUDED.preferred_roles,
    dealbreakers    = EXCLUDED.dealbreakers,
    updated_at      = now();
