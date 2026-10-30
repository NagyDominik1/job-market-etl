"""Database tests for migration 011: the dashboard writes the tracker.

authenticated may add applications, edit the columns it typed in, and append
events (the trigger then sets status and next action); it may not set the
status directly, edit or delete events, or delete applications. anon gets
nothing. They need PostgreSQL and run in a temporary schema (db_conn fixture).
"""

from datetime import date

import psycopg
import pytest

from src.migrate import apply_migrations


@pytest.fixture
def conn(db_conn):
    """Like on Supabase: both roles may look into the schema; one saved application."""
    apply_migrations(db_conn)
    schema = db_conn.execute("SELECT current_schema()").fetchone()[0]
    db_conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO anon, authenticated")
    db_conn.execute("INSERT INTO applications (company_name, position) VALUES ('ErdSoft d.o.o.', 'PHP Developer')")
    return db_conn


def as_role(conn, role, sql, params=None):
    conn.execute(f"SET ROLE {role}")
    try:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else None
    finally:
        conn.execute("RESET ROLE")


def application(conn, app_id=1):
    return conn.execute(
        "SELECT status, applied_at, next_action_date, notes, deadline FROM applications WHERE id = %s",
        (app_id,),
    ).fetchone()


# --- authenticated: what the dashboard does -------------------------------------------------

def test_authenticated_adds_an_application_with_status_saved(conn):
    rows = as_role(conn, "authenticated",
                   "INSERT INTO applications (company_name, position, city, posting_url, deadline, job_id) "
                   "VALUES ('Nordeus', 'Data Engineer', 'Beograd', 'https://nordeus.com/jobs/1', '2026-12-01', NULL) "
                   "RETURNING id, status, company_key")
    assert rows[0][1:] == ("saved", "nordeus")


def test_authenticated_edits_its_own_columns(conn):
    as_role(conn, "authenticated", "UPDATE applications SET notes = 'HR: Kati', deadline = '2026-12-01' WHERE id = 1")
    assert application(conn)[3:] == ("HR: Kati", date(2026, 12, 1))


def test_an_event_from_the_dashboard_runs_the_trigger(conn):
    # The trigger updates status/applied_at/next_action_* although authenticated
    # has no UPDATE on those columns: it runs as its owner (SECURITY DEFINER).
    as_role(conn, "authenticated",
            "INSERT INTO application_events (application_id, event_type, occurred_at) VALUES (1, 'sent', '2026-11-02')")
    assert application(conn)[:3] == ("sent", date(2026, 11, 2), date(2026, 11, 23))


def test_nobody_can_call_the_trigger_function_directly(conn):
    # 012: SECURITY DEFINER, so EXECUTE is revoked; the trigger above still fires.
    for role in ("anon", "authenticated"):
        assert conn.execute(
            "SELECT has_function_privilege(%s, 'apply_application_event()', 'EXECUTE')", (role,)
        ).fetchone() == (False,)


def test_follow_up_counting_works_for_the_dashboard_too(conn):
    for event_type in ("sent", "follow_up_sent", "follow_up_sent"):
        as_role(conn, "authenticated",
                "INSERT INTO application_events (application_id, event_type) VALUES (1, %s)", (event_type,))
    assert application(conn)[0] == "no_response"


def test_authenticated_can_compute_a_company_key(conn):
    # Used by the duplicate warning before adding an application.
    assert as_role(conn, "authenticated", "SELECT normalize_company_key('ERDSOFT Kft.')") == [("erdsoft",)]


# --- authenticated: what it must not do ------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "UPDATE applications SET status = 'offer'",                  # status only through events
    "UPDATE applications SET next_action_date = NULL",
    "UPDATE applications SET applied_at = '2026-01-01'",
    "INSERT INTO applications (company_name, status) VALUES ('X', 'sent')",
    "UPDATE application_events SET body = 'x'",                  # the event log is append-only
    "DELETE FROM application_events",
    "DELETE FROM applications",
])
def test_authenticated_cannot_bypass_the_event_log(conn, sql):
    conn.execute("INSERT INTO application_events (application_id, event_type, body) VALUES (1, 'note', 'n')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn, "authenticated", sql)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "nordeus.com/jobs", "data:text/html,x"])
def test_only_http_links_can_be_saved(conn, url):
    with pytest.raises(psycopg.errors.CheckViolation):
        as_role(conn, "authenticated", "UPDATE applications SET posting_url = %s WHERE id = 1", (url,))


def test_links_are_optional(conn):
    as_role(conn, "authenticated", "INSERT INTO applications (company_name) VALUES ('Open Kft.')")   # NULL is fine


# --- anon: nothing ------------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "INSERT INTO applications (company_name) VALUES ('X')",
    "UPDATE applications SET notes = 'x'",
    "INSERT INTO application_events (application_id, event_type) VALUES (1, 'note')",
])
def test_anon_cannot_write(conn, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn, "anon", sql)


def test_anon_cannot_write_even_with_a_grant(conn):
    # Supabase's default "GRANT ALL ... TO anon" coming back: the policies
    # are TO authenticated only, so RLS still refuses the new row.
    conn.execute("GRANT INSERT ON applications, application_events TO anon")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn, "anon", "INSERT INTO applications (company_name) VALUES ('X')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn, "anon", "INSERT INTO application_events (application_id, event_type) VALUES (1, 'note')")
