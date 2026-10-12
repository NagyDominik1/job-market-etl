"""Shared values used by enrichment (rules and Claude), the profile and scoring.

Kept in one small module without heavy imports, so the profile loader and
the scoring code don't need to import the Anthropic SDK.
"""

# Must match the CHECK constraints on job_enrichment in migrations/001_initial_v2.sql.
SENIORITY_LEVELS = ["intern", "junior", "mid", "senior", "lead", "unknown"]
CATEGORIES = [
    "data", "backend", "frontend", "fullstack", "devops", "mobile",
    "security", "ai_ml", "qa", "design", "non_tech", "other",
]

# Common spelling variants -> one canonical name, so "postgres" and
# "PostgreSQL" are counted as the same skill. Keys are lowercase.
# The canonical names are the ones src/rules.py produces.
SKILL_ALIASES = {
    "python": "Python",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "sql": "SQL",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "react": "React",
    "reactjs": "React",
    "react.js": "React",
    "node": "Node.js",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "golang": "Go",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "docker": "Docker",
    "aws": "AWS",
    "amazon web services": "AWS",
    "gcp": "GCP",
    "google cloud": "GCP",
    "azure": "Azure",
    "microsoft azure": "Azure",
    "java": "Java",
    "c#": "C#",
    "csharp": "C#",
    ".net": ".NET",
    "dotnet": ".NET",
    "asp.net": ".NET",
    ".net core": ".NET",
    "php": "PHP",
    "mysql": "MySQL",
    "mariadb": "MySQL",
    "rest": "REST",
    "rest api": "REST",
    "restful": "REST",
    "restful api": "REST",
    "html": "HTML",
    "html5": "HTML",
    "css": "CSS",
    "css3": "CSS",
    "git": "Git",
    "ci/cd": "CI/CD",
    "unity": "Unity",
    "unity3d": "Unity",
    "excel": "Excel",
    "ms excel": "Excel",
    "microsoft excel": "Excel",
}


def canonical_skill(skill: str) -> str:
    """Return the canonical spelling of a skill, e.g. "postgres" -> "PostgreSQL"."""
    return SKILL_ALIASES.get(skill.strip().lower(), skill.strip())


def skill_alternatives(profile_skill: str) -> set[str]:
    """Canonical skill names that count as a match for one profile entry.

    "REST API" -> {"REST"}. An entry with "/" that is not itself a known skill
    lists alternatives: "HTML/CSS" -> {"HTML", "CSS"}. ("CI/CD" is a known
    skill name, so it is not split.)
    """
    if profile_skill.strip().lower() in SKILL_ALIASES or "/" not in profile_skill:
        return {canonical_skill(profile_skill)}
    return {canonical_skill(part) for part in profile_skill.split("/") if part.strip()}
