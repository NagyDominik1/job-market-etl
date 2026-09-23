"""Database connection helper.

Reads the connection settings from environment variables. For local
development they come from the .env file in the project root.
"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

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


def get_connection() -> psycopg.Connection:
    """Open a new connection to the PostgreSQL database.

    Use it in a "with" block, which commits on success, rolls back on error
    and always closes the connection:

        with get_connection() as conn:
            conn.execute("SELECT 1")
    """
    # Collect ALL missing variables first, so the error lists them together.
    missing = [name for name in REQUIRED_VARIABLES if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Missing environment variable(s): {', '.join(missing)}. "
            "Copy .env.example to .env and fill in the values."
        )

    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=10,  # seconds; fail fast if the database isn't running
    )
