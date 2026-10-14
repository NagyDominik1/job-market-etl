"""Tests for the connection settings (src/db.py). No database, no network:
connection_settings() only reads environment variables.
"""

import pytest

from src.db import connection_settings

LOCAL = {
    "POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": "5432", "POSTGRES_DB": "job_market",
    "POSTGRES_USER": "local_user", "POSTGRES_PASSWORD": "local_password",
}
# A made-up URL in the shape of a Supabase session-pooler URL.
URL = "postgresql://postgres.abcdef:not-a-real-password@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"


@pytest.fixture
def env(monkeypatch):
    for name in [*LOCAL, "DATABASE_URL"]:
        monkeypatch.delenv(name, raising=False)

    def set_env(**values):
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return set_env


def test_local_postgres_variables_are_used_without_database_url(env):
    env(**LOCAL)
    conninfo, kwargs = connection_settings()
    assert conninfo == ""
    assert kwargs["host"] == "127.0.0.1" and kwargs["user"] == "local_user"
    assert "sslmode" not in kwargs  # local Docker: unchanged, no TLS


def test_database_url_takes_precedence_and_requires_ssl(env):
    env(**LOCAL, DATABASE_URL=URL)
    conninfo, kwargs = connection_settings()
    assert conninfo == URL
    assert kwargs["sslmode"] == "require"
    assert "host" not in kwargs  # none of the POSTGRES_* values are mixed in


def test_database_url_alone_is_enough(env):
    env(DATABASE_URL=URL)
    assert connection_settings()[0] == URL


def test_sslmode_in_the_url_is_respected(env):
    env(DATABASE_URL=URL + "?sslmode=verify-full")
    assert "sslmode" not in connection_settings()[1]  # the URL's stricter setting wins


def test_blank_database_url_is_ignored(env):
    env(**LOCAL, DATABASE_URL="   ")
    assert connection_settings()[1]["host"] == "127.0.0.1"


def test_invalid_url_error_does_not_leak_the_password(env):
    env(DATABASE_URL="postgresql://user:secret-pw@host:5432/db?nonsense_option=1")
    with pytest.raises(RuntimeError) as error:
        connection_settings()
    assert "secret-pw" not in str(error.value)


def test_nothing_configured_lists_what_is_missing(env):
    with pytest.raises(RuntimeError, match="Set DATABASE_URL"):
        connection_settings()
