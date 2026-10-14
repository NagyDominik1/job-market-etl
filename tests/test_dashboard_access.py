"""Tests for migration 005: who may read what through Supabase's REST API.

Runs on the local database, in a temporary schema (db_conn fixture). The
migration creates the Supabase roles "anon" and "authenticated" if they are
missing; SET ROLE then lets us act as them, exactly like PostgREST does.
"""

import psycopg
import pytest

from src.migrate import apply_migrations


@pytest.fixture
def conn(db_conn):
    apply_migrations(db_conn)
    schema = db_conn.execute("SELECT current_schema()").fetchone()[0]
    db_conn.execute("INSERT INTO sources (name, kind) VALUES ('arbeitnow', 'api')")
    db_conn.execute(
        "INSERT INTO jobs (fingerprint, title, company_name, location) "
        "VALUES ('fp1', 'Junior PHP Developer', 'Acme Kft.', 'Szeged')"
    )
    db_conn.execute(
        "INSERT INTO raw_postings (source_id, external_id, payload, job_id) "
        """VALUES (1, 'a', '{"secret": "raw data"}', 1)"""
    )
    db_conn.execute(
        "INSERT INTO job_enrichment (job_id, seniority, category, skills, model, match_score, match_reasons) "
        "VALUES (1, 'junior', 'backend', '{PHP}', 'rule-based', 87, '{\"+ PHP (3)\"}')"
    )
    db_conn.execute("INSERT INTO etl_runs (step, status) VALUES ('extract', 'success')")
    # Like on Supabase: both roles may look into the schema (what they may
    # read inside it is decided by the GRANTs and policies of migration 005).
    db_conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO anon, authenticated")
    db_conn.schema = schema
    return db_conn


def as_role(conn, role, sql):
    """Run one query as `role` (like a REST API request), then switch back."""
    conn.execute(f"SET ROLE {role}")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.execute("RESET ROLE")


# --- authenticated (me, logged in) -------------------------------------------------

def test_authenticated_reads_the_job_feed(conn):
    rows = as_role(conn, "authenticated", "SELECT title, match_score, match_reasons, sources FROM job_feed")
    assert rows == [("Junior PHP Developer", 87, ["+ PHP (3)"], "arbeitnow")]


@pytest.mark.parametrize("sql", [
    "SELECT payload FROM raw_postings",                         # the raw data: not granted
    "SELECT * FROM etl_runs",                                   # internal tables: not granted
    "SELECT * FROM schema_migrations",
    "INSERT INTO jobs (fingerprint, title) VALUES ('x', 'x')",  # read-only
    "UPDATE jobs SET title = 'hacked'",
    "DELETE FROM job_enrichment",
])
def test_authenticated_cannot_do_anything_else(conn, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn, "authenticated", sql)


# --- anon (not logged in) -------------------------------------------------------------

def test_anon_has_no_access(conn):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn, "anon", "SELECT * FROM job_feed")


def test_anon_sees_zero_rows_even_with_a_grant(conn):
    # Simulate Supabase's default "GRANT ALL ... TO anon" coming back: RLS
    # still hides every row, because anon has no policy.
    conn.execute("GRANT SELECT ON jobs, job_enrichment, sources, raw_postings, job_feed TO anon")
    assert as_role(conn, "anon", "SELECT count(*) FROM jobs") == [(0,)]
    assert as_role(conn, "anon", "SELECT count(*) FROM job_feed") == [(0,)]


# --- why security_invoker matters ------------------------------------------------------

def test_a_view_without_security_invoker_would_bypass_rls(conn):
    # Supabase's default: anon gets SELECT on everything (RLS still applies).
    conn.execute("GRANT SELECT ON jobs, job_enrichment, sources, raw_postings, job_feed TO anon")
    # The same data through a normal view (runs as its owner, who bypasses RLS):
    conn.execute("CREATE VIEW unsafe_feed AS SELECT title FROM jobs")
    conn.execute("GRANT SELECT ON unsafe_feed TO anon")

    assert as_role(conn, "anon", "SELECT count(*) FROM unsafe_feed") == [(1,)]  # leaked!
    assert as_role(conn, "anon", "SELECT count(*) FROM job_feed") == [(0,)]     # protected
