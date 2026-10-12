"""Shared pytest fixtures.

Most tests need no network and no database. The few that need PostgreSQL use
the `db_conn` fixture: it is skipped automatically when no database is
reachable (e.g. `docker compose up -d postgres` was not run).
"""

import uuid

import psycopg
import pytest

from src.db import get_connection


@pytest.fixture(scope="session")
def database_available() -> bool:
    """Check ONCE per test session whether the database is reachable."""
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
        pytest.skip("PostgreSQL is not reachable (start it with: docker compose up -d postgres)")

    schema = f"test_{uuid.uuid4().hex[:12]}"
    with get_connection() as conn:
        conn.autocommit = True
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}")
        try:
            yield conn
        finally:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")
