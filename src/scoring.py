"""Match score: how well a job fits my profile (0-100), with the reasons.

Deterministic, plain Python, never Claude: the same job and profile always
give the same score, it costs nothing, it can be unit-tested, and every point
is explained in match_reasons. (Claude only helps EXTRACT the facts a job
states, in src/enrich.py; the judgement "is this good for me" stays here.)

Components (the weights add up to 100):
    skills     40  my level of each skill the job asks for
    seniority  25  the job's level vs. the levels I'm looking for
    city       20  my cities in priority order; remote counts as a match
    role       15  one of my preferred roles appears in the title
Any dealbreaker (title keyword, too many years required, a required language
I don't speak) makes the score 0.

Reasons look like: "+ PHP (3)", "~ seniority unknown", "- Berlin",
"- requires 3+ years: score 0".  + = earned points, ~ = partial/unknown,
- = no points (or a dealbreaker).

Scores are recomputed for ALL jobs on every run (it takes well under a
second), so a change in config/profile.yaml applies immediately.
"""

import logging
import re
from dataclasses import dataclass

import psycopg

from src.normalize import REMOTE, normalize_city, normalize_title
from src.user_profile import Profile
from src.vocabulary import canonical_skill, skill_alternatives

logger = logging.getLogger(__name__)

SKILLS_WEIGHT = 40
SENIORITY_WEIGHT = 25
CITY_WEIGHT = 20
ROLE_WEIGHT = 15

# Matching this many skills at my top level (3) gives the full 40 points, so a
# job listing 10 technologies isn't punished for the ones it merely mentions.
SKILLS_FOR_FULL_POINTS = 5
# The last city in my list still gets this share of the city points.
LOWEST_PRIORITY_SHARE = 0.5

SENIORITY_ORDER = ["intern", "junior", "mid", "senior", "lead"]


@dataclass(frozen=True)
class JobFacts:
    """What the score needs about one job (from jobs + job_enrichment)."""

    title: str
    location: str | None
    remote: bool | None
    seniority: str
    skills: list[str]
    years_required: int | None
    required_languages: list[str]


def contains_phrase(text: str, phrase: str) -> bool:
    """True if `phrase` appears in `text` as whole words (both already normalized)."""
    return bool(phrase) and re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


# --- Components ---------------------------------------------------------------------

def score_skills(job: JobFacts, profile: Profile) -> tuple[float, list[str]]:
    # canonical skill name -> (name as written in my profile, my level)
    mine = {}
    for name, level in profile.skills.items():
        for alternative in skill_alternatives(name):
            mine[alternative.lower()] = (name, level)

    job_skills = list(dict.fromkeys(canonical_skill(skill) for skill in job.skills))  # unique, in order
    if not job_skills:
        return 0, ["~ no skills found in the posting"]

    matched = {}  # profile name -> level ("HTML" and "CSS" both count once for "HTML/CSS")
    missing = []
    for skill in job_skills:
        if skill.lower() in mine:
            name, level = mine[skill.lower()]
            matched[name] = level
        else:
            missing.append(skill)

    earned = sum(level / 3 for level in matched.values())
    points = SKILLS_WEIGHT * min(1.0, earned / min(len(job_skills), SKILLS_FOR_FULL_POINTS))

    # Strongest matches first.
    reasons = [f"+ {name} ({level})" for name, level in sorted(matched.items(), key=lambda item: -item[1])]
    if missing:
        reasons.append("- not in my skills: " + ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else ""))
    return points, reasons


def score_seniority(job: JobFacts, profile: Profile) -> tuple[float, list[str]]:
    if not profile.seniority:
        return SENIORITY_WEIGHT, []
    if job.seniority in profile.seniority:
        return SENIORITY_WEIGHT, [f"+ {job.seniority}"]
    if job.seniority not in SENIORITY_ORDER:  # "unknown"
        return SENIORITY_WEIGHT / 2, ["~ seniority unknown"]

    wanted = [SENIORITY_ORDER.index(level) for level in profile.seniority if level in SENIORITY_ORDER]
    distance = min(abs(SENIORITY_ORDER.index(job.seniority) - index) for index in wanted)
    if distance == 1:  # one step away, e.g. "mid" when I look for "junior"
        return 10, [f"~ {job.seniority} (one level away)"]
    return 0, [f"- {job.seniority}"]


def score_city(job: JobFacts, profile: Profile) -> tuple[float, list[str]]:
    job_city = normalize_city(job.location, job.remote)
    my_cities = [normalize_city(city) for city in profile.cities]

    if job_city in my_cities:
        index = my_cities.index(job_city)
        last = max(len(my_cities) - 1, 1)
        share = 1 - (1 - LOWEST_PRIORITY_SHARE) * index / last
        label = REMOTE if job_city == REMOTE else profile.cities[index]
        return CITY_WEIGHT * share, [f"+ {label} (priority {index + 1})"]
    if job_city == REMOTE:
        # Remote is always reachable, even if I didn't list it.
        return CITY_WEIGHT * LOWEST_PRIORITY_SHARE, [f"+ {REMOTE}"]
    if not job_city:
        return 0, ["~ location unknown"]
    return 0, [f"- {job.location}"]


def score_role(job: JobFacts, profile: Profile) -> tuple[float, list[str]]:
    title = normalize_title(job.title)
    for role in profile.preferred_roles:
        if contains_phrase(title, normalize_title(role)):
            return ROLE_WEIGHT, [f"+ role: {role}"]
    return 0, []


def find_dealbreakers(job: JobFacts, profile: Profile) -> list[str]:
    rules = profile.dealbreakers
    reasons = []
    title = normalize_title(job.title)
    for keyword in rules.title_keywords:
        if contains_phrase(title, normalize_title(keyword)):
            reasons.append(f"- title contains '{keyword}': score 0")
    if rules.min_years_required and job.years_required is not None and job.years_required >= rules.min_years_required:
        reasons.append(f"- requires {job.years_required}+ years: score 0")
    for language in job.required_languages:
        if language in rules.required_languages_missing:
            reasons.append(f"- requires {language.capitalize()}: score 0")
    return reasons


def score_job(job: JobFacts, profile: Profile) -> tuple[int, list[str]]:
    """Return (score 0-100, reasons)."""
    total = 0.0
    reasons = []
    for component in (score_skills, score_seniority, score_city, score_role):
        points, component_reasons = component(job, profile)
        total += points
        reasons += component_reasons

    dealbreakers = find_dealbreakers(job, profile)
    if dealbreakers:
        return 0, reasons + dealbreakers
    return round(total), reasons


# --- Scoring every job in the database ----------------------------------------------

SELECT_JOBS_SQL = """
    SELECT e.job_id, j.title, j.location, j.remote,
           e.seniority, e.skills, e.years_required, e.required_languages
    FROM jobs AS j
    JOIN job_enrichment AS e ON e.job_id = j.id
"""

# IS DISTINCT FROM: only write rows whose score or reasons actually changed.
# (Like "<>", but it also treats NULL correctly: NULL is distinct from 42.)
UPDATE_SCORE_SQL = """
    UPDATE job_enrichment
    SET match_score = %(score)s, match_reasons = %(reasons)s
    WHERE job_id = %(job_id)s
      AND (match_score, match_reasons) IS DISTINCT FROM (%(score)s::smallint, %(reasons)s::text[])
"""


def score_all_jobs(conn: psycopg.Connection, profile: Profile) -> int:
    """Recompute the match score of every enriched job; return how many were scored."""
    rows = conn.execute(SELECT_JOBS_SQL).fetchall()
    updates = []
    for job_id, title, location, remote, seniority, skills, years, languages in rows:
        facts = JobFacts(title, location, remote, seniority, skills, years, languages)
        score, reasons = score_job(facts, profile)
        updates.append({"job_id": job_id, "score": score, "reasons": reasons})

    with conn.transaction():
        with conn.cursor() as cursor:
            # executemany sends all updates in one batch (psycopg "pipeline mode").
            cursor.executemany(UPDATE_SCORE_SQL, updates)
    logger.info("Scored %d jobs with profile %s", len(updates), profile.path.name)
    return len(updates)
