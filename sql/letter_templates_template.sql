-- The six letter_templates rows (migration 016): the default templates.
-- Copy it into Supabase -> SQL Editor and run it as it is: there is nothing
-- personal in it (the skills come from search_profile when a letter is
-- drafted). Later changes: the "Sablonok" tab on the dashboard.
--
-- Since the natural letters, Claude writes the letter body; a template gives
-- the greeting (first line) and the closing (last line), and the whole
-- template is the letter only when Claude's body fails the checks twice.
-- The subject is set by draft-letter (the posting title / "Nyílt jelentkezés –
-- junior fejlesztő"...); the subjects below are the same rule, for reference.
--
-- Running it again overwrites the six templates (ON CONFLICT), including
-- changes made in the Sablonok tab.
--
-- Placeholders the Edge Function "draft-letter" fills (keep them as they are):
--   {company}        the company name
--   {position}       the position (posting letters only)
--   {skills}         my skills: the ones the posting asks for first, max 4
--                    ("C#, PHP és MySQL")
--   {tailored_line}  ONE sentence written for this posting/company, in the letter's language
-- Hungarian: write "a {company}" / "A {position}"; the function picks "a" or
-- "az" for the actual word (never "A(z)").
-- Rules: at most 5 sentences, {tailored_line} counts as one; the greeting
-- line and the closing ("Üdvözlettel,") don't count. No name or phone number
-- at the end: the e-mail signature adds them.

INSERT INTO letter_templates (language, kind, subject, body) VALUES
('hu', 'posting', '{position}',
'Tisztelt Hölgyem/Uram!

A {position} pozícióra jelentkezem a {company} csapatába. Fő eszközeim: {skills}. {tailored_line} Az önéletrajzomat csatoltam, és szívesen bemutatkozom személyesen vagy online is.

Üdvözlettel,'),

('hu', 'open', 'Nyílt jelentkezés – junior fejlesztő',
'Tisztelt Hölgyem/Uram!

Junior fejlesztőként szívesen csatlakoznék a {company} csapatához, akkor is, ha most nincs meghirdetett pozíció. Fő eszközeim: {skills}. {tailored_line} Az önéletrajzomat csatoltam; ha nyílik lehetőség, örömmel bemutatkozom.

Üdvözlettel,'),

('sr', 'posting', '{position}',
'Poštovani,

Prijavljujem se za poziciju {position} u kompaniji {company}. Moji glavni alati su {skills}. {tailored_line} U prilogu je moj CV, i rado ću se predstaviti uživo ili online.

Srdačan pozdrav,'),

('sr', 'open', 'Otvorena prijava – junior programer',
'Poštovani,

Kao junior programer želim da se pridružim timu kompanije {company}, čak i ako trenutno nema otvorene pozicije. Moji glavni alati su {skills}. {tailored_line} U prilogu je moj CV; rado ću se predstaviti ako se ukaže prilika.

Srdačan pozdrav,'),

('en', 'posting', '{position}',
'Dear Hiring Team,

I am applying for the {position} position at {company}. My main tools are {skills}. {tailored_line} My CV is attached, and I would be glad to introduce myself in person or online.

Best regards,'),

('en', 'open', 'Open application – Junior Developer',
'Dear Hiring Team,

I am a junior developer and would like to join {company}, even if there is no open position right now. My main tools are {skills}. {tailored_line} My CV is attached; I would be glad to introduce myself if an opportunity comes up.

Best regards,')

ON CONFLICT (language, kind) DO UPDATE SET
    subject = EXCLUDED.subject,
    body    = EXCLUDED.body;
