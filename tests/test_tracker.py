"""Database tests for migration 006: the application tracker.

Each trigger rule, the 2-follow-up limit, company_key (SQL twin of
normalize_company), the two views and the read-only access. They need
PostgreSQL and run in a temporary schema (db_conn fixture, conftest.py).
"""

from datetime import date, timedelta

import psycopg
import pytest

from src.migrate import apply_migrations
from src.normalize import normalize_company

DAY = timedelta(days=1)
D = date(2026, 11, 2)   # a fixed "occurred_at", so the expected dates are exact


@pytest.fixture
def conn(db_conn):
    apply_migrations(db_conn)
    return db_conn


def today(conn) -> date:
    """The database's local_today() (Budapest time), the base of both views."""
    return conn.execute("SELECT local_today()").fetchone()[0]


def new_application(conn, company="ErdSoft d.o.o.", deadline=None, status="saved") -> int:
    return conn.execute(
        "INSERT INTO applications (company_name, position, deadline, status) "
        "VALUES (%s, 'PHP Developer', %s, %s) RETURNING id",
        (company, deadline, status),
    ).fetchone()[0]


def event(conn, app_id, event_type, occurred_at=D, stated_date=None):
    """Insert one event, then return the application's (status, next_action_date, reason)."""
    conn.execute(
        "INSERT INTO application_events (application_id, event_type, occurred_at, stated_date) "
        "VALUES (%s, %s, %s, %s)",
        (app_id, event_type, occurred_at, stated_date),
    )
    return conn.execute(
        "SELECT status, next_action_date, next_action_reason FROM applications WHERE id = %s",
        (app_id,),
    ).fetchone()


# --- trigger rules -------------------------------------------------------------------------

def test_saved_with_deadline_reminds_two_days_before(conn):
    app = new_application(conn, deadline=date(2026, 11, 20))
    status, next_date, reason = event(conn, app, "saved")
    assert (status, next_date) == ("saved", date(2026, 11, 18))
    assert "határidő" in reason


def test_saved_without_deadline_has_no_next_action(conn):
    app = new_application(conn)
    assert event(conn, app, "saved") == ("saved", None, None)


def test_sent_sets_applied_at_and_reminds_after_three_weeks(conn):
    app = new_application(conn)
    assert event(conn, app, "sent") == ("sent", D + 21 * DAY, "3 hete elküldve, írj emlékeztetőt")
    applied_at = conn.execute("SELECT applied_at FROM applications WHERE id = %s", (app,)).fetchone()[0]
    assert applied_at == D


def test_forwarded_is_in_progress_for_two_weeks(conn):
    app = new_application(conn)
    status, next_date, _ = event(conn, app, "forwarded")
    assert (status, next_date) == ("in_progress", D + 14 * DAY)


def test_stated_date_becomes_the_next_action(conn):
    app = new_application(conn)
    status, next_date, _ = event(conn, app, "stated_date", stated_date=date(2026, 11, 15))
    assert (status, next_date) == ("in_progress", date(2026, 11, 15))


def test_stated_date_event_needs_a_date(conn):
    app = new_application(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        event(conn, app, "stated_date")


@pytest.mark.parametrize("event_type", ["reply", "interview"])
def test_reply_and_interview_use_the_stated_date_if_given(conn, event_type):
    app = new_application(conn)
    status, next_date, _ = event(conn, app, event_type, stated_date=date(2026, 11, 9))
    assert (status, next_date) == ("in_progress", date(2026, 11, 9))


@pytest.mark.parametrize("event_type", ["reply", "interview"])
def test_reply_and_interview_otherwise_two_weeks(conn, event_type):
    app = new_application(conn)
    status, next_date, _ = event(conn, app, event_type)
    assert (status, next_date) == ("in_progress", D + 14 * DAY)


@pytest.mark.parametrize("event_type, expected", [("rejection", "rejected"), ("offer", "offer")])
def test_rejection_and_offer_end_the_follow_ups(conn, event_type, expected):
    app = new_application(conn)
    event(conn, app, "sent")
    assert event(conn, app, event_type) == (expected, None, None)


def test_note_changes_nothing(conn):
    app = new_application(conn)
    before = event(conn, app, "sent")
    assert event(conn, app, "note", occurred_at=D + 5 * DAY) == before


# --- the 2-follow-up limit -------------------------------------------------------------

def test_second_follow_up_without_answer_means_no_response(conn):
    app = new_application(conn)
    event(conn, app, "sent")
    first = event(conn, app, "follow_up_sent", occurred_at=D + 21 * DAY)
    assert first[:2] == ("sent", D + 35 * DAY)   # status kept, 2 more weeks
    assert event(conn, app, "follow_up_sent", occurred_at=D + 35 * DAY) == ("no_response", None, None)


def test_a_reply_resets_the_follow_up_count(conn):
    app = new_application(conn)
    event(conn, app, "sent")
    event(conn, app, "follow_up_sent")
    event(conn, app, "reply")
    # Only ONE follow-up since the reply: not given up yet.
    status, next_date, _ = event(conn, app, "follow_up_sent", occurred_at=D + 20 * DAY)
    assert (status, next_date) == ("in_progress", D + 34 * DAY)


def test_updated_at_moves_forward(conn):
    app = new_application(conn)
    created, = conn.execute("SELECT updated_at FROM applications WHERE id = %s", (app,)).fetchone()
    event(conn, app, "sent")   # autocommit: a new transaction, so now() is later
    updated, = conn.execute("SELECT updated_at FROM applications WHERE id = %s", (app,)).fetchone()
    assert updated > created


def test_events_are_deleted_with_their_application(conn):
    app = new_application(conn)
    event(conn, app, "sent")
    conn.execute("DELETE FROM applications WHERE id = %s", (app,))
    assert conn.execute("SELECT count(*) FROM application_events").fetchone() == (0,)


# --- company_key ------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "ErdSoft d.o.o.", "ErdSoft Kft.", "ERDSOFT GmbH & Co. KG", "Nordeus d.o.o. Beograd",
    "AD Systems", "Example.com", "Példa Bt.", "Kft.", "  Ünnepi   Űrhajó Zrt. ",
    "Ћирилица Софт д.о.о.", "Љубљана Њива Џеп ДОО", "Đorđe Đukić Inc", "Straße GmbH",
    "Groß & Søn A/S", "Łódź Tech Sp. z o.o.", "Čačak-Soft, LLC", "Szőlő-Fürt Nyrt.",
    "Acme_Labs Ltd", "", "   ",
])
def test_company_key_matches_python_normalize_company(conn, name):
    sql_key = conn.execute("SELECT normalize_company_key(%s)", (name,)).fetchone()[0]
    assert sql_key == normalize_company(name)


def test_company_key_is_generated_and_read_only(conn):
    new_application(conn, company="ErdSoft d.o.o.")
    new_application(conn, company="ERDSOFT Kft.")
    keys = conn.execute("SELECT DISTINCT company_key FROM applications").fetchall()
    assert keys == [("erdsoft",)]
    with pytest.raises(psycopg.errors.GeneratedAlways):
        conn.execute("INSERT INTO applications (company_name, company_key) VALUES ('X', 'y')")


def test_null_company_key_input_gives_empty_string(conn):
    assert conn.execute("SELECT normalize_company_key(NULL)").fetchone() == ("",)


# --- views --------------------------------------------------------------------------------

def test_todays_actions_lists_only_due_active_applications(conn):
    t = today(conn)
    due = new_application(conn, company="Due Kft.")
    event(conn, due, "sent", occurred_at=t - 21 * DAY)          # next action = today
    overdue = new_application(conn, company="Overdue Kft.")
    event(conn, overdue, "forwarded", occurred_at=t - 20 * DAY)  # 6 days overdue
    later = new_application(conn, company="Later Kft.")
    event(conn, later, "sent", occurred_at=t)                    # in 3 weeks
    rejected = new_application(conn, company="Rejected Kft.")
    conn.execute("UPDATE applications SET next_action_date = %s, status = 'rejected' WHERE id = %s",
                 (t - DAY, rejected))                            # not active

    rows = conn.execute("SELECT company_name, days_overdue FROM todays_actions").fetchall()
    assert rows == [("Overdue Kft.", 6), ("Due Kft.", 0)]       # oldest first


def test_expiring_lists_saved_applications_near_or_past_the_deadline(conn):
    t = today(conn)
    new_application(conn, company="Passed Kft.", deadline=t - DAY)
    new_application(conn, company="Soon Kft.", deadline=t + 3 * DAY)
    new_application(conn, company="Later Kft.", deadline=t + 4 * DAY)
    new_application(conn, company="NoDeadline Kft.")
    new_application(conn, company="Sent Kft.", deadline=t, status="sent")

    rows = conn.execute("SELECT company_name, days_left FROM expiring").fetchall()
    assert rows == [("Passed Kft.", -1), ("Soon Kft.", 3)]


# --- access (RLS + grants) -------------------------------------------------------------------

@pytest.fixture
def conn_with_roles(conn):
    """Like on Supabase: both roles may look into the schema."""
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO anon, authenticated")
    app = new_application(conn, deadline=date(2026, 1, 1))
    event(conn, app, "sent", occurred_at=date(2026, 1, 1))
    return conn


def as_role(conn, role, sql):
    conn.execute(f"SET ROLE {role}")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.execute("RESET ROLE")


@pytest.mark.parametrize("relation", ["applications", "application_events", "todays_actions", "expiring"])
def test_authenticated_can_read(conn_with_roles, relation):
    as_role(conn_with_roles, "authenticated", f"SELECT * FROM {relation}")   # no error


def test_authenticated_sees_the_rows(conn_with_roles):
    assert as_role(conn_with_roles, "authenticated", "SELECT count(*) FROM applications") == [(1,)]
    assert as_role(conn_with_roles, "authenticated", "SELECT count(*) FROM todays_actions") == [(1,)]


@pytest.mark.parametrize("sql", [
    # 011 allows INSERT/UPDATE (tests/test_tracker_write.py); DELETE stays forbidden.
    "DELETE FROM applications",
    "DELETE FROM application_events",
])
def test_authenticated_cannot_delete(conn_with_roles, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn_with_roles, "authenticated", sql)


@pytest.mark.parametrize("relation", ["applications", "application_events", "todays_actions", "expiring"])
def test_anon_has_no_access(conn_with_roles, relation):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn_with_roles, "anon", f"SELECT * FROM {relation}")


def test_anon_sees_zero_rows_even_with_a_grant(conn_with_roles):
    # Supabase's default "GRANT ALL ... TO anon" coming back: RLS still hides every row.
    conn_with_roles.execute("GRANT SELECT ON applications, application_events, todays_actions TO anon")
    assert as_role(conn_with_roles, "anon", "SELECT count(*) FROM applications") == [(0,)]
    assert as_role(conn_with_roles, "anon", "SELECT count(*) FROM application_events") == [(0,)]
    assert as_role(conn_with_roles, "anon", "SELECT count(*) FROM todays_actions") == [(0,)]
