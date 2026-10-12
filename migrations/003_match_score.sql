-- 003: what the Phase 3 enrichment and match score need.
--
-- job_enrichment:
--   years_required     years of experience the posting asks for (NULL = not stated).
--                      Extracted by Claude or by the rules; used by the
--                      "min_years_required" dealbreaker in the profile.
--   required_languages spoken languages the posting REQUIRES (not "nice to have"),
--                      lowercase English names, e.g. {german}. Used by the
--                      "required_languages_missing" dealbreaker.
--   match_reasons      why the job got its match_score, e.g.
--                      {"+ PHP (3)", "+ junior", "- requires 3+ years: score 0"}.
--                      Computed by src/scoring.py (plain Python, never Claude),
--                      recomputed for every job on every run.
ALTER TABLE job_enrichment
    ADD COLUMN years_required     SMALLINT CHECK (years_required BETWEEN 0 AND 50),
    ADD COLUMN required_languages TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN match_reasons      TEXT[] NOT NULL DEFAULT '{}';
