"""Shared pytest fixtures.

Most tests need no network and no database. The few that need PostgreSQL use
the `db_conn` fixture: it is skipped automatically when no database is
reachable (e.g. `docker compose up -d postgres` was not run).

Database tests only ever use the LOCAL database: if DATABASE_URL is set
(e.g. pointing to Supabase), they are skipped, never run against it.
"""

import os
import uuid

import psycopg
import pytest

from src.db import get_connection


@pytest.fixture(scope="session")
def database_available() -> bool:
    """Check ONCE per test session whether the local database is reachable."""
    if os.environ.get("DATABASE_URL"):
        return False  # never run tests against a remote (production) database
    try:
        with get_connection():
            return True
    except (psycopg.Error, RuntimeError):
        return False


@pytest.fixture
def db_conn(database_available):
    """An autocommit connection that works in a fresh, empty, temporary schema.

    "SET search_path" makes unqualified table names (CREATE TABLE jobs ...) go
    into the temporary schema, so tests never touch the real tables. The schema
    and everything in it is dropped afterwards.
    """
    if not database_available:
        pytest.skip("local PostgreSQL is not reachable (start it with: docker compose up -d postgres; "
                    "DATABASE_URL must not be set)")

    schema = f"test_{uuid.uuid4().hex[:12]}"
    with get_connection(autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}")
        try:
            yield conn
        finally:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")
