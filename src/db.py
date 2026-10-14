"""Database connection helper.

Two ways to say where the database is (environment variables):

1. DATABASE_URL, e.g. postgresql://user:password@host:5432/postgres
   Used for a managed database (Supabase) locally and in GitHub Actions.
   It takes precedence over the POSTGRES_* variables, and the connection is
   encrypted: sslmode=require is added unless the URL sets its own sslmode.
2. POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
   The local Docker database (from the .env file); no TLS needed on localhost.

The URL contains the password, so it is never logged or printed.
"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Copy the values from .env into the environment (os.environ).
# override=False: if a variable is ALREADY set (e.g. in your shell or on a
# server), that value wins and .env does not replace it.
load_dotenv(PROJECT_ROOT / ".env", override=False)

REQUIRED_VARIABLES = [
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
]

CONNECT_TIMEOUT_SECONDS = 10  # fail fast if the database isn't reachable


def connection_settings() -> tuple[str, dict]:
    """Return (conninfo, keyword arguments) for psycopg.connect, from the environment.

    Pure function (no network), so the precedence rules are easy to test.
    """
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        try:
            # Parses the URL (and checks it is one) without connecting.
            url_params = conninfo_to_dict(database_url)
        except psycopg.ProgrammingError:
            # Don't include the URL in the message: it contains the password.
            raise RuntimeError("DATABASE_URL is not a valid PostgreSQL connection URL") from None
        kwargs = {"connect_timeout": CONNECT_TIMEOUT_SECONDS}
        # Over the internet the connection must be encrypted. If the URL asks
        # for something (e.g. the stricter verify-full), respect that.
        if "sslmode" not in url_params:
            kwargs["sslmode"] = "require"
        return database_url, kwargs

    # Collect ALL missing variables first, so the error lists them together.
    missing = [name for name in REQUIRED_VARIABLES if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Missing environment variable(s): {', '.join(missing)}. "
            "Set DATABASE_URL, or copy .env.example to .env and fill in the values."
        )
    return "", {
        "host": os.environ["POSTGRES_HOST"],
        "port": os.environ["POSTGRES_PORT"],
        "dbname": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
    }


def get_connection(autocommit: bool = False) -> psycopg.Connection:
    """Open a new connection to the PostgreSQL database.

    Use it in a "with" block, which commits on success, rolls back on error
    and always closes the connection:

        with get_connection() as conn:
            conn.execute("SELECT 1")

    autocommit=True: every statement is committed on its own, and each
    "with conn.transaction():" block is a real, separate transaction. Without
    it, the first statement silently opens a transaction that stays open, and
    later "with conn.transaction():" blocks become parts of it (savepoints).
    """
    conninfo, kwargs = connection_settings()
    return psycopg.connect(conninfo, autocommit=autocommit, **kwargs)
