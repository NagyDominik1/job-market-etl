-- Template for the six letter_templates rows (migration 016).
-- Copy it into Supabase -> SQL Editor, replace the <...> placeholders with
-- your own values and run it. NEVER commit the filled-in version: this file
-- stays a template (the filled-in copy is sql/letter_templates_local.sql,
-- gitignored). Later changes: the "Sablonok" tab on the dashboard.
--
-- Running it again overwrites the six templates (ON CONFLICT).
--
-- Placeholders the Edge Function "draft-letter" fills (keep them as they are):
--   {company}        the company name
--   {position}       the position (posting letters only)
--   {tailored_line}  ONE sentence written for this posting/company
-- Rules: at most 5 sentences, {tailored_line} counts as one; the greeting
-- line and the closing ("Üdvözlettel,") don't count. No name or phone number
-- at the end: the e-mail signature adds them.
--
-- <SKILLS_HU> / <SKILLS_SR> / <SKILLS_EN>: your main skills as a list, e.g.
-- "C#, PHP, JavaScript és MySQL" / "C#, PHP, JavaScript i MySQL" /
-- "C#, PHP, JavaScript and MySQL".

INSERT INTO letter_templates (language, kind, subject, body) VALUES
('hu', 'posting', 'Jelentkezés – {position}',
'Tisztelt Hölgyem/Uram!

A(z) {position} pozícióra jelentkezem a {company} csapatába. Fő eszközeim: <SKILLS_HU>. {tailored_line} Az önéletrajzomat csatoltam, és szívesen bemutatkozom személyesen vagy online is.

Üdvözlettel,'),

('hu', 'open', 'Nyílt jelentkezés – junior fejlesztő',
'Tisztelt Hölgyem/Uram!

Junior fejlesztőként szívesen csatlakoznék a {company} csapatához, akkor is, ha most nincs meghirdetett pozíció. Fő eszközeim: <SKILLS_HU>. {tailored_line} Az önéletrajzomat csatoltam; ha nyílik lehetőség, örömmel bemutatkozom.

Üdvözlettel,'),

('sr', 'posting', 'Prijava – {position}',
'Poštovani,

Prijavljujem se za poziciju {position} u kompaniji {company}. Moji glavni alati su <SKILLS_SR>. {tailored_line} U prilogu je moj CV, i rado ću se predstaviti uživo ili online.

Srdačan pozdrav,'),

('sr', 'open', 'Otvorena prijava – junior programer',
'Poštovani,

Kao junior programer želim da se pridružim timu kompanije {company}, čak i ako trenutno nema otvorene pozicije. Moji glavni alati su <SKILLS_SR>. {tailored_line} U prilogu je moj CV; rado ću se predstaviti ako se ukaže prilika.

Srdačan pozdrav,'),

('en', 'posting', 'Application – {position}',
'Dear Hiring Team,

I am applying for the {position} position at {company}. My main tools are <SKILLS_EN>. {tailored_line} My CV is attached, and I would be glad to introduce myself in person or online.

Best regards,'),

('en', 'open', 'Open application – junior developer',
'Dear Hiring Team,

I am a junior developer and would like to join {company}, even if there is no open position right now. My main tools are <SKILLS_EN>. {tailored_line} My CV is attached; I would be glad to introduce myself if an opportunity comes up.

Best regards,')

ON CONFLICT (language, kind) DO UPDATE SET
    subject = EXCLUDED.subject,
    body    = EXCLUDED.body;
