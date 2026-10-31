"""Database tests for migration 016: letter templates and the letter cost log.

authenticated may read, add and edit templates (subject/body only), never
delete them or touch letter_drafts except reading; anon gets nothing; the
Edge Function (service_role) reads what it needs and logs drafts. They need
PostgreSQL and run in a temporary schema (db_conn fixture, conftest.py).
"""

from pathlib import Path

import psycopg
import pytest

from src.migrate import apply_migrations

TEMPLATE_SQL = Path(__file__).resolve().parent.parent / "sql" / "letter_templates_template.sql"


@pytest.fixture
def conn(db_conn):
    """Like on Supabase: the roles may look into the schema; one template."""
    apply_migrations(db_conn)
    schema = db_conn.execute("SELECT current_schema()").fetchone()[0]
    db_conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO anon, authenticated, service_role")
    db_conn.execute("INSERT INTO letter_templates (language, kind, subject, body) "
                    "VALUES ('hu', 'open', 'Nyílt jelentkezés', 'Szia,\n\n{tailored_line}\n\nÜdv,')")
    return db_conn


def as_role(conn, role, sql, params=None):
    conn.execute(f"SET ROLE {role}")
    try:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else None
    finally:
        conn.execute("RESET ROLE")


def test_authenticated_reads_adds_and_edits_templates(conn):
    as_role(conn, "authenticated", "INSERT INTO letter_templates (language, kind, subject, body) "
                                   "VALUES ('en', 'posting', 'Application – {position}', 'Hi,\n\nText.\n\nBye,')")
    as_role(conn, "authenticated", "UPDATE letter_templates SET body = 'Új szöveg.' WHERE language = 'hu'")
    rows = as_role(conn, "authenticated", "SELECT language, kind, body FROM letter_templates ORDER BY id")
    assert rows == [("hu", "open", "Új szöveg."), ("en", "posting", "Hi,\n\nText.\n\nBye,")]


def test_editing_a_template_sets_updated_at(conn):
    conn.execute("UPDATE letter_templates SET updated_at = now() - interval '1 day'")
    as_role(conn, "authenticated", "UPDATE letter_templates SET subject = 'Új tárgy'")
    age = conn.execute("SELECT now() - updated_at FROM letter_templates").fetchone()[0]
    assert age.total_seconds() < 60


@pytest.mark.parametrize("sql", [
    "UPDATE letter_templates SET language = 'en'",            # language/kind are fixed once a row exists
    "DELETE FROM letter_templates",
    "INSERT INTO letter_drafts (status) VALUES ('success')",   # only the Edge Function logs drafts
])
def test_authenticated_cannot(conn, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn, "authenticated", sql)


def test_one_template_per_language_and_kind(conn):
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO letter_templates (language, kind, subject, body) VALUES ('hu', 'open', 'x', 'y')")


@pytest.mark.parametrize("values", [
    ("de", "open", "x", "y"),        # unknown language
    ("hu", "letter", "x", "y"),      # unknown kind
    ("en", "open", "x", "   "),      # empty body
    ("en", "open", "x", "y" * 2001), # too long
])
def test_invalid_templates_are_refused(conn, values):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO letter_templates (language, kind, subject, body) VALUES (%s, %s, %s, %s)", values)


@pytest.mark.parametrize("table", ["letter_templates", "letter_drafts"])
def test_anon_has_no_access(conn, table):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn, "anon", f"SELECT * FROM {table}")


def test_service_role_reads_what_it_needs_and_logs_drafts(conn):
    for table in ("letter_templates", "applications", "jobs", "job_enrichment", "search_results", "search_profile"):
        as_role(conn, "service_role", f"SELECT count(*) FROM {table}")
    as_role(conn, "service_role", "INSERT INTO letter_drafts (language, kind, cv_variant, status, input_tokens, output_tokens) "
                                  "VALUES ('hu', 'open', 'hu', 'success', 900, 40)")
    assert as_role(conn, "authenticated", "SELECT input_tokens, output_tokens FROM letter_drafts") == [(900, 40)]


def test_a_draft_survives_its_application(conn):
    app_id = conn.execute("INSERT INTO applications (company_name) VALUES ('Acme') RETURNING id").fetchone()[0]
    conn.execute("INSERT INTO letter_drafts (application_id, status) VALUES (%s, 'success')", (app_id,))
    conn.execute("DELETE FROM applications WHERE id = %s", (app_id,))
    assert conn.execute("SELECT application_id FROM letter_drafts").fetchone() == (None,)


def test_template_file_runs_as_is(conn):
    # Nothing to fill in since the skills became the {skills} placeholder.
    sql = TEMPLATE_SQL.read_text(encoding="utf-8")
    conn.execute(sql)
    conn.execute(sql)   # again: updates the same six rows
    assert conn.execute("SELECT count(*) FROM letter_templates").fetchone() == (6,)
