"""Database tests for migration 014: the job_applied view.

Which pipeline jobs are at a company I already applied to ("Már jelentkeztél"
on the Job feed): same company_key whatever the spelling, or an application
linked to the job. Only authenticated may read it. They need PostgreSQL and
run in a temporary schema (db_conn fixture, conftest.py).
"""

import psycopg
import pytest

from src.migrate import apply_migrations


@pytest.fixture
def conn(db_conn):
    apply_migrations(db_conn)
    schema = db_conn.execute("SELECT current_schema()").fetchone()[0]
    db_conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO anon, authenticated")
    return db_conn


def add_job(conn, fingerprint, company):
    return conn.execute(
        "INSERT INTO jobs (fingerprint, title, company_name) VALUES (%s, 'PHP Developer', %s) RETURNING id",
        (fingerprint, company),
    ).fetchone()[0]


def applied_ids(conn, role=None):
    if role:
        conn.execute(f"SET ROLE {role}")
    try:
        return {row[0] for row in conn.execute("SELECT job_id FROM job_applied").fetchall()}
    finally:
        conn.execute("RESET ROLE")


def test_a_job_at_a_company_i_applied_to_is_listed_whatever_the_spelling(conn):
    erdsoft = add_job(conn, "a", "ERDSOFT Kft.")
    add_job(conn, "b", "Nordeus")
    conn.execute("INSERT INTO applications (company_name) VALUES ('ErdSoft d.o.o.')")
    assert applied_ids(conn) == {erdsoft}


def test_a_job_linked_to_an_application_is_listed(conn):
    job = add_job(conn, "a", None)   # no company name: only the job_id link can match
    conn.execute("INSERT INTO applications (company_name, job_id) VALUES ('Acme', %s)", (job,))
    assert applied_ids(conn) == {job}


def test_no_applications_no_rows(conn):
    add_job(conn, "a", "Nordeus")
    assert applied_ids(conn) == set()


def test_authenticated_reads_it_anon_does_not(conn):
    job = add_job(conn, "a", "Nordeus")
    conn.execute("INSERT INTO applications (company_name) VALUES ('Nordeus')")
    assert applied_ids(conn, "authenticated") == {job}
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        applied_ids(conn, "anon")
