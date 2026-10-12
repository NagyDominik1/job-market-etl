"""Tests for the match score (src/scoring.py): each component and each dealbreaker.

Pure functions with a hand-made profile: no network, no database.
"""

from pathlib import Path

import pytest

from src.scoring import (
    JobFacts,
    score_city,
    score_job,
    score_role,
    score_seniority,
    score_skills,
)
from src.user_profile import Dealbreakers, Profile

PROFILE = Profile(
    skills={"PHP": 3, "MySQL": 3, "REST API": 2, "HTML/CSS": 2, "Python": 1},
    seniority=["intern", "junior"],
    cities=["Szeged", "Subotica", "Novi Sad", "Belgrade", "remote"],
    languages={"hungarian": "native"},
    preferred_roles=["backend", "php developer"],
    dealbreakers=Dealbreakers(
        title_keywords=["senior", "lead"], min_years_required=3, required_languages_missing=["german"],
    ),
    path=Path("test-profile.yaml"),
)


def job(**overrides) -> JobFacts:
    values = dict(
        title="Junior PHP Developer", location="Szeged", remote=None, seniority="junior",
        skills=["PHP", "MySQL"], years_required=None, required_languages=[],
    )
    values.update(overrides)
    return JobFacts(**values)


# --- Skills (40) ------------------------------------------------------------------

@pytest.mark.parametrize("skills, expected", [
    (["PHP", "MySQL"], 40),                 # 2 skills, both at my level 3
    (["PHP", "Kotlin"], 20),                # half of them, at level 3
    (["Python"], 40 / 3),                   # my level 1 = a third of the points
    (["postgres", "Go"], 0),                # none of mine
    (["PHP", "MySQL", "Go", "AWS", "Kotlin", "Rust", "Scala", "Swift", "Ruby", "C++"], 16),  # 2 of max 5 counted
])
def test_skill_points(skills, expected):
    assert score_skills(job(skills=skills), PROFILE)[0] == pytest.approx(expected)


def test_skill_reasons_show_my_level_strongest_first():
    _, reasons = score_skills(job(skills=["Python", "PHP", "Kotlin"]), PROFILE)
    assert reasons == ["+ PHP (3)", "+ Python (1)", "- not in my skills: Kotlin"]


def test_skill_aliases_and_alternatives():
    # "REST" is my "REST API"; HTML and CSS both count once for "HTML/CSS".
    _, reasons = score_skills(job(skills=["REST", "HTML", "CSS"]), PROFILE)
    assert reasons == ["+ REST API (2)", "+ HTML/CSS (2)"]


def test_no_skills_found():
    assert score_skills(job(skills=[]), PROFILE) == (0, ["~ no skills found in the posting"])


# --- Seniority (25) ---------------------------------------------------------------

@pytest.mark.parametrize("seniority, points, reason", [
    ("junior", 25, "+ junior"),
    ("intern", 25, "+ intern"),
    ("unknown", 12.5, "~ seniority unknown"),
    ("mid", 10, "~ mid (one level away)"),
    ("senior", 0, "- senior"),
])
def test_seniority(seniority, points, reason):
    assert score_seniority(job(seniority=seniority), PROFILE) == (points, [reason])


# --- City (20) -------------------------------------------------------------------

@pytest.mark.parametrize("location, remote, points, reason", [
    ("Szeged", None, 20, "+ Szeged (priority 1)"),
    ("Szabadka", None, 17.5, "+ Subotica (priority 2)"),       # alias of my 2nd city
    ("Нови Сад", None, 15, "+ Novi Sad (priority 3)"),          # Cyrillic
    ("Beograd", None, 12.5, "+ Belgrade (priority 4)"),
    ("Remote", None, 10, "+ remote (priority 5)"),
    ("Berlin", True, 10, "+ remote (priority 5)"),              # the remote flag counts
    ("Berlin", False, 0, "- Berlin"),
    (None, None, 0, "~ location unknown"),
])
def test_city_by_priority(location, remote, points, reason):
    assert score_city(job(location=location, remote=remote), PROFILE) == (points, [reason])


def test_remote_counts_even_if_not_listed():
    profile = Profile(**{**PROFILE.__dict__, "cities": ["Szeged"]})
    assert score_city(job(location="Remote"), profile) == (10, ["+ remote"])


# --- Role (15) -------------------------------------------------------------------

@pytest.mark.parametrize("title, points", [
    ("Backend Developer", 15),
    ("Junior PHP Developer (m/w/d)", 15),
    ("Frontend Developer", 0),
    ("PHP Developers wanted", 0),   # whole words only: "developers" is not "developer"
])
def test_role_keywords_in_title(title, points):
    assert score_role(job(title=title), PROFILE)[0] == points


# --- Whole score and dealbreakers ---------------------------------------------------

def test_perfect_match_is_100():
    score, reasons = score_job(job(), PROFILE)
    assert score == 100
    assert reasons == ["+ PHP (3)", "+ MySQL (3)", "+ junior", "+ Szeged (priority 1)", "+ role: php developer"]


def test_score_is_rounded_sum_of_components():
    # skills 20 + seniority 12.5 + city 17.5 + role 0 = 50
    score, _ = score_job(job(title="Developer", skills=["PHP", "Go"], seniority="unknown", location="Subotica"), PROFILE)
    assert score == 50


@pytest.mark.parametrize("overrides, reason", [
    ({"title": "Senior PHP Developer"}, "- title contains 'senior': score 0"),
    ({"title": "Team Lead Backend"}, "- title contains 'lead': score 0"),
    ({"years_required": 3}, "- requires 3+ years: score 0"),
    ({"years_required": 5}, "- requires 5+ years: score 0"),
    ({"required_languages": ["german"]}, "- requires German: score 0"),
])
def test_each_dealbreaker_gives_zero(overrides, reason):
    score, reasons = score_job(job(**overrides), PROFILE)
    assert score == 0
    assert reasons[-1] == reason
    assert "+ PHP (3)" in reasons  # the other reasons are still shown


def test_below_the_limits_is_not_a_dealbreaker():
    assert score_job(job(years_required=2), PROFILE)[0] == 100
    assert score_job(job(required_languages=["french"]), PROFILE)[0] == 100   # not in my list
    assert score_job(job(title="Leadership Trainee PHP Developer"), PROFILE)[0] > 0  # "lead" only as a whole word
