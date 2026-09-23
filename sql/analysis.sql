-- Example analysis queries on the AI-enriched data.
-- Run them all with (bash):        docker compose exec -T postgres psql -U etl_user -d job_market < sql/analysis.sql
-- or (Windows PowerShell):         Get-Content sql/analysis.sql | docker compose exec -T postgres psql -U etl_user -d job_market

-- 1. Top 10 skills overall.
-- unnest() turns the skills array into one row per skill, so we can count them.
SELECT skill, count(*) AS jobs
FROM job_enrichment, unnest(skills) AS skill
GROUP BY skill
ORDER BY jobs DESC, skill
LIMIT 10;

-- 2. Top 10 skills in data jobs.
SELECT skill, count(*) AS jobs
FROM job_enrichment, unnest(skills) AS skill
WHERE category = 'data'
GROUP BY skill
ORDER BY jobs DESC, skill
LIMIT 10;

-- 3. Number of jobs per seniority level, from intern to lead.
-- array_position() gives each level its position in the list, for a natural order.
SELECT seniority, count(*) AS jobs
FROM job_enrichment
GROUP BY seniority
ORDER BY array_position(ARRAY['intern', 'junior', 'mid', 'senior', 'lead', 'unknown'], seniority);
