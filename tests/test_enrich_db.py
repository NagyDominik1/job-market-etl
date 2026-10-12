"""Database tests for the enrich step (src/enrich.py + src/scoring.py).

Claude is never really called: the "with API key" tests replace
classify_job() with a fake. They need PostgreSQL and run in a temporary
schema (db_conn fixture, conftest.py).
"""

from pathlib import Path

import anthropic
import pytest

from src import enrich as enrich_module
from src.enrich import enrich
from src.main import run_enrich
from src.migrate import apply_migrations
from src.rules import RULE_BASED_MODEL
from src.user_profile import Dealbreakers, Profile

PROFILE = Profile(
    skills={"PHP": 3, "MySQL": 3},
    seniority=["junior"],
    cities=["Szeged", "remote"],
    languages={},
    preferred_roles=["php developer"],
    dealbreakers=Dealbreakers(title_keywords=["senior"], min_years_required=3, required_languages_missing=["german"]),
    path=Path("test-profile.yaml"),
)


@pytest.fixture
def conn(db_conn, monkeypatch):
    apply_migrations(db_conn)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ENRICH_LIMIT", raising=False)
    return db_conn


def add_job(conn, title, description="", location="Szeged"):
    return conn.execute(
        "INSERT INTO jobs (fingerprint, title, location, description) VALUES (%s, %s, %s, %s) RETURNING id",
        (f"fp-{title}-{location}", title, location, description),
    ).fetchone()[0]


def enrichment(conn, job_id):
    return conn.execute(
        """
        SELECT model, seniority, skills, years_required, required_languages, match_score, match_reasons
        FROM job_enrichment WHERE job_id = %s
        """,
        (job_id,),
    ).fetchone()


def test_without_api_key_rules_enrich_and_score_every_job(conn):
    good = add_job(conn, "Junior PHP Developer", "PHP and MySQL")
    german = add_job(conn, "Junior PHP Developer", "PHP. Fluent German required.", location="Berlin")
    years = add_job(conn, "PHP Developer", "PHP, 4+ years of experience")

    counts = enrich(conn, PROFILE)

    assert counts == {"rules": 3, "claude": 0, "claude_failed": 0, "scored": 3}
    model, seniority, skills, _, _, score, reasons = enrichment(conn, good)
    assert (model, seniority, skills, score) == (RULE_BASED_MODEL, "junior", ["PHP", "MySQL"], 100)
    assert reasons == ["+ PHP (3)", "+ MySQL (3)", "+ junior", "+ Szeged (priority 1)", "+ role: php developer"]

    assert enrichment(conn, german)[4] == ["german"]
    assert enrichment(conn, german)[5] == 0
    assert enrichment(conn, years)[3] == 4
    assert enrichment(conn, years)[6][-1] == "- requires 4+ years: score 0"


def test_rerun_changes_nothing(conn):
    add_job(conn, "Junior PHP Developer", "PHP")
    enrich(conn, PROFILE)
    assert enrich(conn, PROFILE)["rules"] == 0


def test_profile_change_applies_at_once(conn):
    job = add_job(conn, "Junior PHP Developer", "PHP and MySQL")
    enrich(conn, PROFILE)
    assert enrichment(conn, job)[5] == 100

    stricter = Profile(**{**PROFILE.__dict__, "cities": ["Novi Sad"]})
    enrich(conn, stricter)
    assert enrichment(conn, job)[5] == 80   # the 20 city points are gone


def fake_claude(result):
    def classify_job(client, model, title, description):
        if isinstance(result, Exception):
            raise result
        return result, 100, 20
    return classify_job


CLAUDE_RESULT = {
    "seniority": "junior", "category": "backend", "skills": ["PHP", "MySQL"],
    "years_required": 1, "required_languages": ["english"],
}


def test_with_api_key_claude_upgrades_the_best_jobs_first(conn, monkeypatch):
    best = add_job(conn, "Junior PHP Developer", "PHP and MySQL")
    other = add_job(conn, "Sales Manager", "", location="Berlin")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ENRICH_LIMIT", "1")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test")
    monkeypatch.setattr(enrich_module, "classify_job", fake_claude(CLAUDE_RESULT))

    counts = enrich(conn, PROFILE)

    assert (counts["claude"], counts["claude_failed"]) == (1, 0)
    assert enrichment(conn, best)[0] == "claude-test"          # highest score went to Claude
    assert enrichment(conn, other)[0] == RULE_BASED_MODEL
    assert enrichment(conn, best)[4] == ["english"]


def test_rules_never_overwrite_a_claude_row(conn, monkeypatch):
    job = add_job(conn, "Junior PHP Developer", "PHP")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test")
    monkeypatch.setattr(enrich_module, "classify_job", fake_claude(CLAUDE_RESULT))
    enrich(conn, PROFILE)

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    enrich(conn, PROFILE)
    assert enrichment(conn, job)[0] == "claude-test"


@pytest.mark.parametrize("error", [ValueError("bad JSON"), anthropic.APIConnectionError(request=None)])
def test_failed_claude_call_keeps_the_rule_based_row(conn, monkeypatch, error):
    job = add_job(conn, "Junior PHP Developer", "PHP")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(enrich_module, "classify_job", fake_claude(error))

    counts = enrich(conn, PROFILE)

    assert (counts["claude"], counts["claude_failed"]) == (0, 1)
    model, _, _, _, _, score, _ = enrichment(conn, job)
    assert model == RULE_BASED_MODEL and score is not None   # the fallback is still scored


def test_enrich_run_is_logged(conn):
    add_job(conn, "Junior PHP Developer", "PHP")
    assert run_enrich(conn) is True
    step, status, rows_enriched, message = conn.execute(
        "SELECT step, status, rows_enriched, error_message FROM etl_runs"
    ).fetchone()
    assert (step, status, rows_enriched) == ("enrich", "success", 1)
    assert message.startswith("rules changed 1, Claude 0 (0 failed), scored 1")
