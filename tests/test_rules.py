"""Tests for the rule-based fallback in src/rules.py. Run with:  .venv\\Scripts\\python -m pytest

Plain strings only: no network, no database.
"""

import pytest

from src.enrich import validate_enrichment
from src.rules import classify_category, classify_seniority, classify_with_rules, find_skills


# --- Skills ---------------------------------------------------------------------

def test_skills_are_found_case_insensitively_as_whole_words():
    assert find_skills("Data Engineer", "We use python, AWS and docker.") == ["Python", "AWS", "Docker"]


def test_skills_are_ordered_by_first_appearance_title_first():
    assert find_skills("Kotlin Developer", "Experience with Java and Kotlin.") == ["Kotlin", "Java"]


def test_similar_names_do_not_match_each_other():
    # "Java" is not inside "JavaScript", "SQL" not inside "PostgreSQL"/"MySQL"/"NoSQL".
    assert find_skills("", "JavaScript, PostgreSQL, MySQL and NoSQL") == ["JavaScript", "PostgreSQL", "MySQL"]


def test_aliases_map_to_one_name():
    assert find_skills("", "Postgres, K8s, golang") == ["PostgreSQL", "Kubernetes", "Go"]


def test_go_only_counts_as_a_language_in_a_list():
    assert find_skills("", "Backend in Python, Go and SQL") == ["Python", "Go", "SQL"]
    assert find_skills("", "Languages: Go/Rust") == ["Go", "Rust"]
    assert find_skills("", "Ready to go to market? Let's go!") == []
    assert find_skills("", "Go-Live support") == []


def test_r_only_counts_as_a_language_in_a_list():
    assert find_skills("", "Statistics with R, Python and SQL") == ["R", "Python", "SQL"]
    assert find_skills("", "Tools (R/Python)") == ["R", "Python"]
    assert find_skills("", "R&D department, Section R of the contract") == []


def test_c_plus_plus_c_sharp_and_dotnet():
    assert find_skills("", "C++ and C# on .NET") == ["C++", "C#", ".NET"]
    assert find_skills("", "ASP.NET Core") == [".NET"]
    # a web address is not .NET, and "C" alone is not C++/C#
    assert find_skills("", "Visit www.example.net or try plan C.") == []


def test_words_that_are_also_normal_words_are_case_sensitive():
    assert find_skills("", "We react quickly and excel at rest") == []
    assert find_skills("", "React frontend, Excel reports, REST APIs") == ["React", "Excel", "REST"]


def test_missing_description_is_fine():
    assert find_skills("Python Developer", None) == ["Python"]


# --- Seniority ------------------------------------------------------------------

@pytest.mark.parametrize(
    "title, expected",
    [
        ("Senior Data Engineer", "senior"),
        ("(Senior) Product Engineer", "senior"),
        ("Sr. Backend Developer", "senior"),
        ("Junior Frontend Developer", "junior"),
        ("Graduate Site Reliability Engineer", "junior"),
        ("Lead Product Manager", "lead"),
        ("Head of Customer Experience", "lead"),
        ("Principal Product & Brand Designer", "lead"),
        ("Staff Security Operations Engineer", "lead"),
        ("Teamleiter Logistik", "lead"),
        ("Werkstudent Marketing", "intern"),
        ("Praktikum im Controlling", "intern"),
        ("Service Project Management Intern", "intern"),
        ("Stage de 6 mois UX/UI Designer", "intern"),
        ("Mid-Level Python Developer", "mid"),
        ("Software Engineer", "unknown"),
    ],
)
def test_seniority_from_title_keywords(title, expected):
    assert classify_seniority(title) == expected


def test_seniority_keyword_traps():
    assert classify_seniority("Mid-Market Account Executive") == "unknown"  # not "mid" level
    assert classify_seniority("Member of Marketing Staff") == "unknown"  # not a "staff" role
    assert classify_seniority("Lead Generation Specialist") == "unknown"  # not a "lead" role


# --- Category -------------------------------------------------------------------

@pytest.mark.parametrize(
    "title, expected",
    [
        ("Senior Security Engineer", "security"),
        ("Machine Learning Engineer", "ai_ml"),
        ("AI Researcher", "ai_ml"),
        ("Senior Data Scientist", "data"),
        ("Director, Site Reliability Engineering", "devops"),
        ("AWS Cloud Infrastructure Architect", "devops"),
        ("iOS Developer", "mobile"),
        ("QA Engineer", "qa"),
        ("Senior Product Designer", "design"),
        ("Full-Stack Developer", "fullstack"),
        ("Senior Frontend Engineer", "frontend"),
        ("Backend Engineer (Go)", "backend"),
        ("Enterprise Account Executive - UK", "non_tech"),
        ("Werkstudent im Vertrieb", "non_tech"),
        ("Marketing Manager", "non_tech"),
        ("Performance Marketer", "non_tech"),
        ("Staplerfahrer", "non_tech"),
        ("Software Engineer", "other"),
        ("Residential Architect", "other"),
    ],
)
def test_category_from_title_keywords(title, expected):
    assert classify_category(title) == expected


# --- Whole result ---------------------------------------------------------------

def test_rule_based_result_passes_validation():
    result = classify_with_rules(
        "Senior Data Engineer",
        "Python, SQL, Airflow, dbt, Snowflake, AWS, Docker, Kubernetes, Terraform, Git, Kafka, Spark",
    )
    cleaned = validate_enrichment(result)
    assert cleaned["seniority"] == "senior"
    assert cleaned["category"] == "data"
    assert len(cleaned["skills"]) == 10  # 12 found, cut to the maximum
    assert cleaned["skills"][:3] == ["Python", "SQL", "Airflow"]
