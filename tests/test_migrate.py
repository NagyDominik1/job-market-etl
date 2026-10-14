"""Tests for the SQL migration runner (src/migrate.py).

The first group only reads files; the second group needs PostgreSQL and runs
in a temporary schema (see the db_conn fixture in conftest.py).
"""

import uuid

import psycopg
import pytest

from src.migrate import MIGRATIONS_DIR, apply_migrations, find_migrations


def write(directory, name, sql):
    (directory / name).write_text(sql, encoding="utf-8", newline="\n")


# --- Reading migration files ---------------------------------------------------

def test_migrations_are_sorted_by_version(tmp_path):
    write(tmp_path, "002_second.sql", "SELECT 2;")
    write(tmp_path, "001_first.sql", "SELECT 1;")
    write(tmp_path, "010_tenth.sql", "SELECT 10;")
    assert [m.version for m in find_migrations(tmp_path)] == ["001", "002", "010"]


def test_bad_file_name_is_rejected(tmp_path):
    write(tmp_path, "1_too_short.sql", "SELECT 1;")
    with pytest.raises(ValueError, match="Bad migration file name"):
        find_migrations(tmp_path)


def test_duplicate_version_is_rejected(tmp_path):
    write(tmp_path, "001_a.sql", "SELECT 1;")
    write(tmp_path, "001_b.sql", "SELECT 1;")
    with pytest.raises(ValueError, match="Duplicate"):
        find_migrations(tmp_path)


def test_checksum_changes_when_the_file_changes(tmp_path):
    write(tmp_path, "001_a.sql", "SELECT 1;")
    before = find_migrations(tmp_path)[0].checksum
    write(tmp_path, "001_a.sql", "SELECT 2;")
    assert find_migrations(tmp_path)[0].checksum != before


def test_real_migration_files_are_valid():
    migrations = find_migrations(MIGRATIONS_DIR)
    assert migrations[0].filename == "001_initial_v2.sql"


# --- Applying migrations (needs PostgreSQL) ----------------------------------------

def table_exists(conn, name):
    # to_regclass() returns NULL if no such table exists in the search_path.
    return conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is not None


def test_pending_migrations_are_applied_once(db_conn, tmp_path):
    write(tmp_path, "001_a.sql", "CREATE TABLE a (id INT);")
    write(tmp_path, "002_b.sql", "CREATE TABLE b (id INT);")

    assert apply_migrations(db_conn, tmp_path) == ["001", "002"]
    assert table_exists(db_conn, "a") and table_exists(db_conn, "b")

    # Second run: nothing to do (a CREATE TABLE run twice would fail).
    assert apply_migrations(db_conn, tmp_path) == []

    # A new file is picked up on the next run.
    write(tmp_path, "003_c.sql", "CREATE TABLE c (id INT);")
    assert apply_migrations(db_conn, tmp_path) == ["003"]


def test_failing_migration_is_rolled_back_completely(db_conn, tmp_path):
    write(tmp_path, "001_ok.sql", "CREATE TABLE ok (id INT);")
    # The first statement works, the second one fails.
    write(tmp_path, "002_broken.sql", "CREATE TABLE half (id INT);\nCREATE TABLE ok (id INT);")

    with pytest.raises(psycopg.errors.DuplicateTable):
        apply_migrations(db_conn, tmp_path)

    assert table_exists(db_conn, "ok")          # 001 stays applied
    assert not table_exists(db_conn, "half")    # 002 left no trace...
    versions = [row[0] for row in db_conn.execute("SELECT version FROM schema_migrations")]
    assert versions == ["001"]                  # ...and is not recorded


def test_editing_an_applied_migration_is_detected(db_conn, tmp_path):
    write(tmp_path, "001_a.sql", "CREATE TABLE a (id INT);")
    apply_migrations(db_conn, tmp_path)

    write(tmp_path, "001_a.sql", "CREATE TABLE a (id BIGINT);")
    with pytest.raises(RuntimeError, match="changed after it was applied"):
        apply_migrations(db_conn, tmp_path)


def test_real_migrations_create_the_v2_schema(db_conn):
    apply_migrations(db_conn)
    for table in ["sources", "raw_postings", "jobs", "job_enrichment", "etl_runs"]:
        assert table_exists(db_conn, table), table


# --- 004: Row Level Security -----------------------------------------------------------

V2_TABLES = ["sources", "raw_postings", "jobs", "job_enrichment", "etl_runs", "schema_migrations"]


def test_row_level_security_is_enabled_on_every_table(db_conn):
    apply_migrations(db_conn)
    rows = db_conn.execute(
        """
        SELECT c.relname, c.relrowsecurity
        FROM pg_class c
        WHERE c.relnamespace = current_schema()::regnamespace AND c.relkind = 'r'
        """
    ).fetchall()
    assert dict(rows) == {table: True for table in V2_TABLES}


def test_rls_without_policies_hides_rows_from_other_roles_but_not_the_owner(db_conn):
    apply_migrations(db_conn)
    db_conn.execute("INSERT INTO sources (name, kind) VALUES ('arbeitnow', 'api')")

    # A role like Supabase's "anon": it may SELECT, but owns nothing.
    role = f"rls_test_{uuid.uuid4().hex[:8]}"
    try:
        db_conn.execute(f"CREATE ROLE {role} NOLOGIN")
    except psycopg.errors.InsufficientPrivilege:
        pytest.skip("the local database user may not create roles")
    try:
        db_conn.execute(f"GRANT USAGE ON SCHEMA {db_conn.execute('SELECT current_schema()').fetchone()[0]} TO {role}")
        db_conn.execute(f"GRANT SELECT ON sources TO {role}")

        db_conn.execute(f"SET ROLE {role}")
        try:
            hidden = db_conn.execute("SELECT count(*) FROM sources").fetchone()[0]
        finally:
            db_conn.execute("RESET ROLE")
        owner_sees = db_conn.execute("SELECT count(*) FROM sources").fetchone()[0]

        assert hidden == 0      # RLS on, no policy: no rows, not even an error
        assert owner_sees == 1  # the pipeline (owner) is not affected
    finally:
        db_conn.execute(f"DROP OWNED BY {role}")
        db_conn.execute(f"DROP ROLE {role}")
