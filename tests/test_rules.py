"""Tests for the rule-based fallback in src/rules.py. Run with:  .venv\\Scripts\\python -m pytest

Plain strings only: no network, no database.
"""

import pytest

from src.enrich import validate_enrichment
from src.rules import (
    classify_category,
    classify_seniority,
    classify_with_rules,
    detect_required_languages,
    detect_years_required,
    find_skills,
)


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


# --- Hungarian and Serbian (Latin and Cyrillic) -----------------------------------

@pytest.mark.parametrize(
    "title, expected",
    [
        ("Gyakornok - PHP fejlesztő", "intern"),
        ("PHP fejlesztő gyakornoki program", "intern"),
        ("Pályakezdő Java fejlesztő", "junior"),
        ("Junior szoftverfejlesztő", "junior"),
        ("Medior PHP fejlesztő", "mid"),
        ("Senior .NET fejlesztő", "senior"),
        ("Csoportvezető", "lead"),
        ("Praksa - backend", "intern"),
        ("Praktikant za QA", "intern"),
        ("Stažista u IT sektoru", "intern"),
        ("Стажиста за развој софтвера", "intern"),
        ("Početnik Python programer", "junior"),
        ("Јуниор програмер", "junior"),
        ("Medior PHP programer", "mid"),
        ("Сениор Java инжењер", "senior"),
    ],
)
def test_hungarian_and_serbian_seniority(title, expected):
    assert classify_seniority(title) == expected


@pytest.mark.parametrize("title, expected", [("Adatmérnök", "data"), ("Inženjer podataka", "data")])
def test_hungarian_and_serbian_category(title, expected):
    assert classify_category(title) == expected


# --- Years of experience ------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("3+ years of experience with PHP", 3),
        ("Minimum 5 yrs in backend", 5),
        ("min. 4 years", 4),
        ("at least 2 years", 2),
        ("3 years of professional experience", 3),
        ("2-3 years experience", 2),            # a range: the lower bound
        ("2 to 4 years of experience", 2),
        ("3+ év tapasztalat", 3),
        ("Legalább 2 év szakmai tapasztalat", 2),
        ("2–4 év tapasztalat", 2),
        ("3+ godine iskustva", 3),
        ("najmanje 3 godine radnog iskustva", 3),
        ("Минимум 3 године искуства", 3),
        ("mindestens 3 Jahre Berufserfahrung", 3),
        ("2+ years of Python, 5+ years overall", 5),  # the largest is the real requirement
    ],
)
def test_years_of_experience(text, expected):
    assert detect_years_required(text) == expected


@pytest.mark.parametrize("text", ["18+ years old", "founded 25 years ago", "no numbers here", None])
def test_text_without_an_experience_requirement(text):
    assert detect_years_required(text) is None


def test_years_decide_seniority_when_the_title_does_not():
    assert classify_with_rules("Software Engineer", "1+ year of experience")["seniority"] == "junior"
    assert classify_with_rules("Software Engineer", "4+ years of experience")["seniority"] == "mid"
    assert classify_with_rules("Software Engineer", "6+ years of experience")["seniority"] == "senior"
    # The title wins over the years.
    assert classify_with_rules("Junior Developer", "6+ years of experience")["seniority"] == "junior"


# --- Required languages ---------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "Fluent German required",
        "Sehr gute Deutschkenntnisse (C1)",
        "Fließend Deutsch in Wort und Schrift",
        "You have excellent German and English language skills (C1)",
        "Business-fluent German",
        "Tárgyalóképes német nyelvtudás",
        "Znanje nemačkog jezika",
        "Знање немачког језика",
        "Wir suchen dich für unser Team und du bist mit der Entwicklung von Software vertraut. " * 3,
    ],
)
def test_german_is_required(text):
    assert detect_required_languages(text) == ["german"]


@pytest.mark.parametrize(
    "text",
    [
        "German is a plus",                           # optional
        "Deutschkenntnisse von Vorteil",
        "Znanje nemačkog jezika je prednost",
        "Office in Germany. Fluent English.",         # the country, not the language
        "Standort Deutschland; fluent English",
        "Németországi iroda, angol nyelvtudás",
        "Rad u Nemačkoj, znanje engleskog",
        "We work with German clients.",               # no requirement cue
        "We serve thousands of businesses, from startups to Deutsche Telekom.",  # a company name
        "German fluency would be ideal for local paperwork.",                   # a wish, not a must
    ],
)
def test_german_is_not_required(text):
    assert detect_required_languages(text) == []


def test_profile_skills_are_detected():
    text = "C#, .NET, Unity3D, PHP, MySQL, REST API, React, JavaScript, HTML/CSS, Git, Java, Python, Docker, PostgreSQL"
    assert set(find_skills("Developer", text)) >= {
        "C#", ".NET", "Unity", "PHP", "MySQL", "REST", "React", "JavaScript", "HTML", "CSS",
        "Git", "Java", "Python", "Docker", "PostgreSQL",
    }
