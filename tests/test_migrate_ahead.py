"""Migration runner: a database that is AHEAD of the code (src/migrate.py).

Phase 9 applies a branch's migration to Supabase before main has the file.
The daily run on main must then neither fail nor undo anything. Needs
PostgreSQL (db_conn fixture, conftest.py).
"""

from src.migrate import apply_migrations


def write(directory, name, sql):
    (directory / name).write_text(sql, encoding="utf-8", newline="\n")


def table_exists(conn, name):
    return conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is not None


def test_database_ahead_of_the_code_is_fine(db_conn, tmp_path):
    write(tmp_path, "001_a.sql", "CREATE TABLE a (id INT);")
    write(tmp_path, "002_b.sql", "CREATE TABLE b (id INT);")
    apply_migrations(db_conn, tmp_path)

    (tmp_path / "002_b.sql").unlink()       # "main" doesn't know 002 yet
    assert apply_migrations(db_conn, tmp_path) == []
    assert table_exists(db_conn, "b")
