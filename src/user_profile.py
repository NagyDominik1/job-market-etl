"""My job-search profile (config/profile.yaml), used by the match score.

config/profile.yaml holds my real profile and is gitignored.
config/profile.example.yaml has the same structure with made-up values and is
committed. If profile.yaml is missing (e.g. a fresh clone), the example is
used and a warning is logged, so the pipeline still runs.

Setting (environment variable, optional):
    PROFILE_PATH  another profile file to use (it must exist)

(The module is not called "profile" because Python has a built-in module
with that name.)
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.vocabulary import SENIORITY_LEVELS

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
PROFILE_FILE = CONFIG_DIR / "profile.yaml"
EXAMPLE_PROFILE_FILE = CONFIG_DIR / "profile.example.yaml"


@dataclass(frozen=True)
class Dealbreakers:
    title_keywords: list[str]               # e.g. ["senior", "lead"]
    min_years_required: int | None          # job asks for this many years or more -> 0
    required_languages_missing: list[str]   # job requires one of these languages -> 0


@dataclass(frozen=True)
class Profile:
    skills: dict[str, int]                  # skill -> 1 basics, 2 used in a project, 3 confident
    seniority: list[str]                    # levels I'm looking for
    cities: list[str]                       # in priority order; "remote" allowed
    languages: dict[str, str]               # language -> level (informational)
    preferred_roles: list[str]              # keywords looked for in job titles
    dealbreakers: Dealbreakers
    path: Path                              # which file it was loaded from


def _string_list(data: dict, key: str) -> list[str]:
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"'{key}' must be a list of texts")
    return [item.strip() for item in value]


def parse_profile(data, path: Path) -> Profile:
    """Check the YAML content and turn it into a Profile. Raises ValueError with a clear message."""
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: the file must contain a YAML mapping (key: value lines)")
    try:
        skills = data.get("skills") or {}
        if not isinstance(skills, dict):
            raise ValueError("'skills' must be a mapping like 'Python: 2'")
        for skill, level in skills.items():
            # bool is a subclass of int, so "yes" (= True in YAML) would pass as 1 without this.
            if isinstance(level, bool) or level not in (1, 2, 3):
                raise ValueError(f"skill {skill!r}: level must be 1, 2 or 3, got {level!r}")

        seniority = [level.lower() for level in _string_list(data, "seniority")]
        unknown = [level for level in seniority if level not in SENIORITY_LEVELS]
        if unknown:
            raise ValueError(f"unknown seniority level(s) {unknown}; allowed: {SENIORITY_LEVELS}")

        languages = data.get("languages") or {}
        if not isinstance(languages, dict):
            raise ValueError("'languages' must be a mapping like 'english: fluent'")

        raw_dealbreakers = data.get("dealbreakers") or {}
        if not isinstance(raw_dealbreakers, dict):
            raise ValueError("'dealbreakers' must be a mapping")
        min_years = raw_dealbreakers.get("min_years_required")
        if min_years is not None and (isinstance(min_years, bool) or not isinstance(min_years, int) or min_years < 1):
            raise ValueError(f"'min_years_required' must be a whole number of 1 or more, got {min_years!r}")

        return Profile(
            skills={str(skill).strip(): level for skill, level in skills.items()},
            seniority=seniority,
            cities=_string_list(data, "cities"),
            languages={str(k).lower(): str(v) for k, v in languages.items()},
            preferred_roles=_string_list(data, "preferred_roles"),
            dealbreakers=Dealbreakers(
                title_keywords=_string_list(raw_dealbreakers, "title_keywords"),
                min_years_required=min_years,
                required_languages_missing=[
                    lang.lower() for lang in _string_list(raw_dealbreakers, "required_languages_missing")
                ],
            ),
            path=path,
        )
    except ValueError as error:
        raise ValueError(f"{path.name}: {error}") from None


def load_profile(path: Path | None = None) -> Profile:
    """Load the profile: `path`, else PROFILE_PATH, else config/profile.yaml,
    else (with a warning) config/profile.example.yaml.
    """
    explicit = path or os.environ.get("PROFILE_PATH")
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            # I asked for this file explicitly, so a typo should be an error.
            raise FileNotFoundError(f"Profile file {path} does not exist")
    elif PROFILE_FILE.is_file():
        path = PROFILE_FILE
    else:
        logger.warning(
            "%s not found, using the example profile %s. Copy it to profile.yaml and edit it.",
            PROFILE_FILE.name, EXAMPLE_PROFILE_FILE.name,
        )
        path = EXAMPLE_PROFILE_FILE

    with open(path, encoding="utf-8") as f:
        # safe_load only builds plain data (dicts, lists, strings, numbers);
        # yaml.load could create arbitrary Python objects from a crafted file.
        data = yaml.safe_load(f)
    return parse_profile(data, path)
