"""Minimal SQL migration runner (no Alembic, no SQLAlchemy).

A migration is a plain SQL file in migrations/, named NNN_description.sql
(e.g. 001_initial_v2.sql). The runner:
  1. creates the schema_migrations table if it doesn't exist,
  2. finds the files that are not recorded there yet,
  3. applies them in filename order, EACH IN ITS OWN TRANSACTION, and records
     each one (version, filename, checksum) in the same transaction.

Postgres can roll back DDL (CREATE TABLE, ALTER TABLE...), unlike MySQL. So a
migration that fails halfway leaves NO trace: no half-created tables and no
schema_migrations row. Fix the file and run again.

Applied files must never change: their checksum is stored, and the runner
stops with an error if a file no longer matches. Add a new file instead.

Run it on its own with:
    .venv\\Scripts\\python -m src.migrate
(The pipeline, src/main.py, also runs it automatically on startup.)
"""

import hashlib
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

from src.db import get_connection

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

# "001_initial_v2.sql" -> version "001". Exactly three digits, then a name.
MIGRATION_FILE_PATTERN = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version    TEXT PRIMARY KEY,
        filename   TEXT NOT NULL,
        checksum   TEXT NOT NULL,   -- SHA-256 of the file, to detect later edits
        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""


@dataclass(frozen=True)
class Migration:
    version: str   # "001"
    filename: str  # "001_initial_v2.sql"
    sql: str
    checksum: str


# --- Reading the migration files (no database: easy to test) ----------------

def find_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Return all migration files, sorted by version.

    Raises ValueError for a .sql file with a wrong name or a duplicate version,
    instead of silently skipping it.
    """
    migrations = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = MIGRATION_FILE_PATTERN.match(path.name)
        if not match:
            raise ValueError(
                f"Bad migration file name {path.name!r}: expected NNN_description.sql, "
                "e.g. 002_add_index.sql (lowercase letters, digits and _ only)"
            )
        # Read bytes, so the checksum is exactly the file content. (.gitattributes
        # forces LF line endings, so the bytes are the same on Windows and Linux.)
        content = path.read_bytes()
        migrations.append(Migration(
            version=match.group(1),
            filename=path.name,
            sql=content.decode("utf-8"),
            checksum=hashlib.sha256(content).hexdigest(),
        ))

    versions = [m.version for m in migrations]
    duplicates = sorted({v for v in versions if versions.count(v) > 1})
    if duplicates:
        raise ValueError(f"Duplicate migration version(s): {', '.join(duplicates)}")
    return migrations


# --- Applying them -------------------------------------------------------------

def get_applied(conn: psycopg.Connection) -> dict[str, str]:
    """Return {version: checksum} of the migrations already applied."""
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return dict(rows)


def apply_migrations(conn: psycopg.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply all pending migrations and return the versions applied in this call.

    `conn` must be in autocommit mode, so that each "with conn.transaction()"
    below is a real, separate transaction (and not a part of one big one).
    """
    if not conn.autocommit:
        raise ValueError("apply_migrations needs a connection with autocommit=True")

    migrations = find_migrations(migrations_dir)
    conn.execute(CREATE_TABLE_SQL)

    # Stop if an already-applied file was edited: the database was built from
    # the OLD content, so the files would no longer describe the real schema.
    applied = get_applied(conn)
    for migration in migrations:
        if migration.version in applied and applied[migration.version] != migration.checksum:
            raise RuntimeError(
                f"Migration {migration.filename} was changed after it was applied. "
                "Undo the change and put it in a NEW migration file instead."
            )

    newly_applied = []
    for migration in migrations:
        if migration.version in applied:
            continue
        with conn.transaction():
            # Lock the table so two pipeline runs starting at the same moment
            # can't apply the same migration twice: the second one waits here,
            # then sees the version below and skips it.
            conn.execute("LOCK TABLE schema_migrations IN EXCLUSIVE MODE")
            if conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = %s", (migration.version,)
            ).fetchone():
                continue
            logger.info("Applying migration %s", migration.filename)
            # Without parameters, psycopg can run a file with many statements at once.
            conn.execute(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, filename, checksum) VALUES (%s, %s, %s)",
                (migration.version, migration.filename, migration.checksum),
            )
        newly_applied.append(migration.version)

    if newly_applied:
        logger.info("Applied %d migration(s): %s", len(newly_applied), ", ".join(newly_applied))
    else:
        logger.info("Database schema is up to date (%d migration(s))", len(migrations))
    return newly_applied


def migrate() -> list[str]:
    """Open a connection and apply all pending migrations."""
    with get_connection(autocommit=True) as conn:
        return apply_migrations(conn)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        migrate()
    except (psycopg.Error, OSError, ValueError, RuntimeError) as error:
        logger.error("Migration failed: %s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
