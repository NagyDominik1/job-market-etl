"""Database tests for migration 017: posting facts for tailored letters.

search_results and applications get requirements (max 5) and a company
sentence; search_profile gets my projects. The dashboard may insert the facts
with a new application ("Hozzáadom") but not edit them; draft-letter
(service_role) saves what it extracted. They need PostgreSQL and run in a
temporary schema (db_conn fixture, conftest.py).
"""

import json

import psycopg
import pytest

from src.migrate import apply_migrations


@pytest.fixture
def conn(db_conn):
    apply_migrations(db_conn)
    schema = db_conn.execute("SELECT current_schema()").fetchone()[0]
    db_conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO anon, authenticated, service_role")
    return db_conn


def as_role(conn, role, sql, params=None):
    conn.execute(f"SET ROLE {role}")
    try:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else None
    finally:
        conn.execute("RESET ROLE")


def test_defaults_mean_not_looked_up_yet(conn):
    app = conn.execute("INSERT INTO applications (company_name) VALUES ('Acme') RETURNING requirements, company_summary")
    assert app.fetchone() == (None, None)          # NULL: draft-letter may open the posting once
    run_id = conn.execute("INSERT INTO search_runs (mode) VALUES ('jobs') RETURNING id").fetchone()[0]
    row = conn.execute("INSERT INTO search_results (run_id, kind, company_name) VALUES (%s, 'job', 'Acme') "
                       "RETURNING requirements, company_summary", (run_id,)).fetchone()
    assert row == ([], None)
    assert conn.execute("SELECT projects FROM search_profile").fetchall() == []
    conn.execute("INSERT INTO search_profile DEFAULT VALUES")
    assert conn.execute("SELECT projects FROM search_profile").fetchone() == ([],)


@pytest.mark.parametrize("sql", [
    "INSERT INTO applications (company_name, requirements) VALUES ('Acme', '{a,b,c,d,e,f}')",
    "INSERT INTO applications (company_name, company_summary) VALUES ('Acme', repeat('x', 301))",
    "INSERT INTO search_profile (projects) VALUES ('{\"name\": \"not a list\"}')",
])
def test_limits_are_checked(conn, sql):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(sql)


def test_hozzaadom_inserts_the_facts_but_cannot_edit_them(conn):
    as_role(conn, "authenticated",
            "INSERT INTO applications (company_name, position, requirements, company_summary) "
            "VALUES ('Acme', 'PHP Developer', '{PHP 8,REST}', 'Acme builds shops.')")
    assert conn.execute("SELECT requirements, company_summary FROM applications").fetchone() == (
        ["PHP 8", "REST"], "Acme builds shops.")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn, "authenticated", "UPDATE applications SET requirements = '{}'")


def test_draft_letter_saves_extracted_facts(conn):
    conn.execute("INSERT INTO applications (company_name, posting_url) VALUES ('Acme', 'https://acme.example/job/1')")
    as_role(conn, "service_role",
            "UPDATE applications SET requirements = '{Python,SQL}', company_summary = 'Acme makes tools.'")
    assert conn.execute("SELECT requirements FROM applications").fetchone() == (["Python", "SQL"],)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn, "service_role", "UPDATE applications SET status = 'offer'")   # only the facts


def test_authenticated_reads_the_new_columns(conn):
    conn.execute("INSERT INTO search_profile (projects) VALUES (%s)",
                 (json.dumps([{"name": "Personal Trainer", "kind": "project", "skills": ["PHP"]}]),))
    rows = as_role(conn, "authenticated", "SELECT projects->0->>'name' FROM search_profile")
    assert rows == [("Personal Trainer",)]
    as_role(conn, "authenticated", "SELECT requirements, company_summary FROM applications")
    as_role(conn, "authenticated", "SELECT requirements, company_summary FROM search_results")
