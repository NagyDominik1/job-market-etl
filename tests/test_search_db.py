"""Database tests for migration 007: AI search tables.

The rate limit (1 search per hour, race-free), already_applied, the URL
check, the single-row profile, and who may read/write what. They need
PostgreSQL and run in a temporary schema (db_conn fixture, conftest.py).
"""

import json
import re
import threading
from pathlib import Path

import psycopg
import pytest

from src.db import get_connection
from src.migrate import apply_migrations


@pytest.fixture
def conn(db_conn):
    apply_migrations(db_conn)
    return db_conn


def start_run(conn, mode="jobs", params=None):
    """Call start_search_run(); returns (run_id, next_allowed_at)."""
    return conn.execute(
        "SELECT run_id, next_allowed_at FROM start_search_run(%s, %s)",
        (mode, json.dumps(params or {})),
    ).fetchone()


def add_result(conn, run_id, company="ErdSoft d.o.o.", url="https://example.com/job"):
    return conn.execute(
        "INSERT INTO search_results (run_id, kind, title, company_name, url, score) "
        "VALUES (%s, 'job', 'PHP Developer', %s, %s, 80) RETURNING already_applied",
        (run_id, company, url),
    ).fetchone()[0]


# --- rate limit ---------------------------------------------------------------------------

def test_first_search_starts_a_run(conn):
    run_id, next_allowed = start_run(conn, "companies", {"cities": ["Szeged"]})
    assert run_id is not None and next_allowed is None
    row = conn.execute("SELECT mode, params, status FROM search_runs WHERE id = %s", (run_id,)).fetchone()
    assert row == ("companies", {"cities": ["Szeged"]}, "running")


def test_second_search_within_an_hour_is_refused(conn):
    first, _ = start_run(conn)
    second, next_allowed = start_run(conn, "companies")
    assert second is None
    started = conn.execute("SELECT started_at FROM search_runs WHERE id = %s", (first,)).fetchone()[0]
    assert (next_allowed - started).total_seconds() == 3600
    assert conn.execute("SELECT count(*) FROM search_runs").fetchone() == (1,)


def test_search_is_allowed_again_after_an_hour(conn):
    first, _ = start_run(conn)
    conn.execute("UPDATE search_runs SET started_at = now() - interval '61 minutes' WHERE id = %s", (first,))
    assert start_run(conn)[0] is not None


def test_a_failed_run_that_cost_nothing_does_not_count(conn):
    first, _ = start_run(conn)
    conn.execute("UPDATE search_runs SET status = 'error', input_tokens = 0 WHERE id = %s", (first,))
    assert start_run(conn)[0] is not None


def test_a_failed_run_that_cost_tokens_does_not_count_either(conn):
    # Since 008: every failed run can be retried at once (the spend limit on
    # the Anthropic key is the cost cap).
    first, _ = start_run(conn)
    conn.execute("UPDATE search_runs SET status = 'error', input_tokens = 5000 WHERE id = %s", (first,))
    assert start_run(conn)[0] is not None


def test_a_run_stuck_in_running_is_closed_as_timeout_and_does_not_count(conn):
    # 009: the Edge Function was killed and never finished its run (013: no activity for 5 min).
    stuck, _ = start_run(conn)
    conn.execute("UPDATE search_runs SET started_at = now() - interval '6 minutes', "
                 "last_activity_at = now() - interval '6 minutes' WHERE id = %s", (stuck,))
    assert start_run(conn)[0] is not None
    status, error, finished = conn.execute(
        "SELECT status, error, finished_at FROM search_runs WHERE id = %s", (stuck,)).fetchone()
    assert status == "error" and error.startswith("timeout") and finished is not None


def test_a_recent_running_run_still_counts(conn):
    # 4 minutes: maybe still working, so a second search must wait.
    first, _ = start_run(conn)
    conn.execute("UPDATE search_runs SET started_at = now() - interval '4 minutes' WHERE id = %s", (first,))
    assert start_run(conn)[0] is None
    assert conn.execute("SELECT status FROM search_runs WHERE id = %s", (first,)).fetchone() == ("running",)


def test_run_counts_have_defaults_and_reject_negative_values(conn):
    # 010: a new run starts with zero counts and candidates NULL (no answer yet).
    run_id, _ = start_run(conn)
    row = conn.execute(
        "SELECT fetches_used, candidates, dropped_unconfirmed, dropped_senior, dropped_years, "
        "dropped_dealbreaker, dropped_bad_url, dropped_other, stopped_early FROM search_runs WHERE id = %s",
        (run_id,),
    ).fetchone()
    assert row == (0, None, 0, 0, 0, 0, 0, 0, False)
    conn.execute(
        "UPDATE search_runs SET fetches_used = 5, candidates = 7, dropped_unconfirmed = 2, "
        "dropped_senior = 1, stopped_early = true WHERE id = %s", (run_id,))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE search_runs SET dropped_years = -1 WHERE id = %s", (run_id,))


def test_a_successful_run_still_counts(conn):
    first, _ = start_run(conn)
    conn.execute("UPDATE search_runs SET status = 'success', input_tokens = 5000 WHERE id = %s", (first,))
    assert start_run(conn)[0] is None


def test_two_simultaneous_searches_start_only_one_run(conn):
    # Two connections call start_search_run at the same moment. Without the
    # advisory lock both could see "no run yet" and both insert.
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    barrier = threading.Barrier(2)
    results = []

    def worker():
        with get_connection(autocommit=True) as other:
            other.execute(f"SET search_path TO {schema}")
            barrier.wait()
            results.append(start_run(other)[0])

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(r is None for r in results) == [False, True]   # one started, one refused
    assert conn.execute("SELECT count(*) FROM search_runs").fetchone() == (1,)


# --- search_results -------------------------------------------------------------------------

def test_already_applied_is_set_from_applications(conn):
    run_id, _ = start_run(conn)
    conn.execute("INSERT INTO applications (company_name) VALUES ('ERDSOFT Kft.')")
    assert add_result(conn, run_id, company="ErdSoft d.o.o.") is True    # same company_key
    assert add_result(conn, run_id, company="Nordeus") is False


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://example.com", "example.com", "data:text/html,x"])
def test_only_http_links_are_stored(conn, url):
    run_id, _ = start_run(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        add_result(conn, run_id, url=url)


def test_score_must_be_0_to_100(conn):
    run_id, _ = start_run(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO search_results (run_id, kind, company_name, score) VALUES (%s, 'company', 'X', 101)",
            (run_id,),
        )


def test_results_are_deleted_with_their_run(conn):
    run_id, _ = start_run(conn)
    add_result(conn, run_id)
    conn.execute("DELETE FROM search_runs WHERE id = %s", (run_id,))
    assert conn.execute("SELECT count(*) FROM search_results").fetchone() == (0,)


# --- search_profile ---------------------------------------------------------------------------

def test_search_profile_has_at_most_one_row(conn):
    conn.execute("INSERT INTO search_profile (cities) VALUES ('{Szeged}')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO search_profile (cities) VALUES ('{Budapest}')")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO search_profile (id) VALUES (false)")


def test_profile_template_is_valid_sql_once_filled_in(conn):
    # The committed template, with its placeholders replaced by dummy values.
    template = Path(__file__).resolve().parent.parent / "sql" / "search_profile_template.sql"
    if not template.exists():
        pytest.skip("sql/ is not copied into the pipeline image")
    sql = template.read_text(encoding="utf-8")
    sql = sql.replace("<NUMBER>", "5")
    sql = re.sub(r"<[A-Z0-9_]+>", "x", sql)
    conn.execute(sql)
    conn.execute(sql)   # the second run updates the same row
    assert conn.execute("SELECT count(*) FROM search_profile").fetchone() == (1,)


# --- access (RLS + grants) ------------------------------------------------------------------------

@pytest.fixture
def conn_with_roles(conn):
    """Like on Supabase: the roles may look into the schema; one run with one result."""
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO anon, authenticated, service_role")
    run_id, _ = start_run(conn)
    add_result(conn, run_id)
    conn.execute("INSERT INTO search_profile (cities) VALUES ('{Szeged}')")
    return conn


def as_role(conn, role, sql, params=None):
    conn.execute(f"SET ROLE {role}")
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.execute("RESET ROLE")


@pytest.mark.parametrize("table", ["search_profile", "search_runs", "search_results"])
def test_authenticated_can_read(conn_with_roles, table):
    assert as_role(conn_with_roles, "authenticated", f"SELECT count(*) FROM {table}") == [(1,)]


@pytest.mark.parametrize("sql", [
    "INSERT INTO search_runs (mode) VALUES ('jobs')",
    "UPDATE search_runs SET searches_used = 0",
    "INSERT INTO search_results (run_id, kind, company_name) VALUES (1, 'job', 'x')",
    "DELETE FROM search_results",
    "UPDATE search_profile SET cities = '{}'",
    "SELECT * FROM start_search_run('jobs', '{}')",   # only the Edge Function may start runs
])
def test_authenticated_cannot_write(conn_with_roles, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn_with_roles, "authenticated", sql)


@pytest.mark.parametrize("table", ["search_profile", "search_runs", "search_results"])
def test_anon_has_no_access(conn_with_roles, table):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn_with_roles, "anon", f"SELECT * FROM {table}")


def test_anon_cannot_start_a_run(conn_with_roles):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role(conn_with_roles, "anon", "SELECT * FROM start_search_run('jobs', '{}')")


def test_service_role_can_do_what_the_edge_function_needs(conn_with_roles):
    conn = conn_with_roles
    conn.execute("UPDATE search_runs SET started_at = now() - interval '2 hours'")   # rate limit passed
    run_id = as_role(conn, "service_role", "SELECT run_id FROM start_search_run('jobs', '{}')")[0][0]
    assert run_id is not None
    assert as_role(conn, "service_role", "SELECT count(*) FROM search_profile") == [(1,)]
    assert as_role(conn, "service_role", "SELECT count(*) FROM applications") == [(0,)]
    as_role(conn, "service_role",
            "INSERT INTO search_results (run_id, kind, company_name, url) "
            "VALUES (%s, 'company', 'Acme', 'https://acme.example') RETURNING id", (run_id,))
    as_role(conn, "service_role",
            "UPDATE search_runs SET status = 'success', searches_used = 3 WHERE id = %s RETURNING id", (run_id,))


# --- chunks (013) ----------------------------------------------------------------------------

def start_chunked(conn, chunks=3):
    run_id, _ = start_run(conn, "jobs", {"cities": ["Szeged", "Subotica"]})
    conn.execute("UPDATE search_runs SET chunks_total = %s WHERE id = %s", (chunks, run_id))
    return run_id


def claim(conn, run_id, chunk):
    return conn.execute("SELECT mode, params, chunks_total FROM claim_search_chunk(%s, %s)", (run_id, chunk)).fetchall()


def test_chunks_are_claimed_once_and_in_order(conn):
    run_id = start_chunked(conn, chunks=2)
    assert claim(conn, run_id, 1) == []                      # not chunk 0 first: refused
    assert claim(conn, run_id, 0) == [("jobs", {"cities": ["Szeged", "Subotica"]}, 2)]
    assert claim(conn, run_id, 0) == []                      # the same chunk again: refused
    assert len(claim(conn, run_id, 1)) == 1
    assert claim(conn, run_id, 2) == []                      # past the last chunk


def test_a_finished_run_has_no_more_chunks(conn):
    run_id = start_chunked(conn)
    conn.execute("UPDATE search_runs SET status = 'success' WHERE id = %s", (run_id,))
    assert claim(conn, run_id, 0) == []


def test_a_run_from_before_013_has_no_chunks(conn):
    run_id, _ = start_run(conn)                               # chunks_total stays NULL
    assert claim(conn, run_id, 0) == []


def test_a_long_run_with_recent_chunk_activity_is_not_stuck(conn):
    # 6 chunks may take ~6 minutes; only inactivity means stuck.
    run_id = start_chunked(conn)
    conn.execute("UPDATE search_runs SET started_at = now() - interval '7 minutes', "
                 "last_activity_at = now() - interval '1 minute' WHERE id = %s", (run_id,))
    assert start_run(conn)[0] is None                         # still running, still counts
    assert conn.execute("SELECT status FROM search_runs WHERE id = %s", (run_id,)).fetchone() == ("running",)


def test_an_abandoned_run_with_results_is_closed_as_success_and_counts(conn):
    run_id = start_chunked(conn)
    add_result(conn, run_id)
    conn.execute("UPDATE search_runs SET started_at = now() - interval '10 minutes', "
                 "last_activity_at = now() - interval '6 minutes' WHERE id = %s", (run_id,))
    assert start_run(conn)[0] is None                         # it gave results: the hour counts
    status, error = conn.execute("SELECT status, error FROM search_runs WHERE id = %s", (run_id,)).fetchone()
    assert status == "success" and "timeout" in error


def test_an_abandoned_run_without_results_is_an_error_and_does_not_count(conn):
    run_id = start_chunked(conn)
    conn.execute("UPDATE search_runs SET error = 'Szeged: timed out', started_at = now() - interval '10 minutes', "
                 "last_activity_at = now() - interval '6 minutes' WHERE id = %s", (run_id,))
    assert start_run(conn)[0] is not None
    status, error = conn.execute("SELECT status, error FROM search_runs WHERE id = %s", (run_id,)).fetchone()
    assert status == "error" and error.startswith("Szeged: timed out; timeout")


def test_only_the_edge_function_may_claim_chunks(conn_with_roles):
    for role in ("anon", "authenticated"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            as_role(conn_with_roles, role, "SELECT * FROM claim_search_chunk(1, 0)")
    conn_with_roles.execute("UPDATE search_runs SET chunks_total = 1")
    assert len(as_role(conn_with_roles, "service_role", "SELECT * FROM claim_search_chunk(1, 0)")) == 1
