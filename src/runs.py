"""The etl_runs table: a log of every pipeline step.

One row per source for the extract step (source_id set), and one row per
run for the steps that work on all sources at once (transform, later enrich;
source_id NULL). The `step` column says which one it is.

Every function takes an autocommit connection and commits its own short
transaction, so a run's row is saved immediately and does not depend on the
data work: even if the data work is rolled back, the 'failed' record written
here stays in the table.
"""

import psycopg

# A run still 'running' after this long was not finished properly (the process
# was killed, the machine lost power...). A normal run takes about a minute.
STUCK_RUN_AFTER = "1 hour"


def start_run(conn: psycopg.Connection, step: str, source_id: int | None = None) -> int:
    """Insert a 'running' row for this step and return its id."""
    with conn.transaction():
        # RETURNING id gives us back the id the database generated.
        return conn.execute(
            "INSERT INTO etl_runs (step, source_id, status) VALUES (%s, %s, 'running') RETURNING id",
            (step, source_id),
        ).fetchone()[0]


def finish_run_success(
    conn: psycopg.Connection,
    run_id: int,
    rows_extracted: int,
    rows_loaded: int,
    message: str | None = None,
) -> None:
    """Mark the run as successful and store the row counts.

    `message` is stored in error_message: a successful transform uses it to
    say how many single postings could not be transformed.
    """
    with conn.transaction():
        conn.execute(
            """
            UPDATE etl_runs
            SET finished_at = now(), status = 'success',
                rows_extracted = %s, rows_loaded = %s, error_message = %s
            WHERE id = %s
            """,
            (rows_extracted, rows_loaded, message, run_id),
        )


def finish_run_failed(conn: psycopg.Connection, run_id: int, error_message: str, rows_extracted: int | None) -> None:
    """Mark the run as failed and store the error message."""
    with conn.transaction():
        conn.execute(
            """
            UPDATE etl_runs
            SET finished_at = now(), status = 'failed',
                rows_extracted = %s, error_message = %s
            WHERE id = %s
            """,
            (rows_extracted, error_message, run_id),
        )


def mark_stuck_runs_failed(conn: psycopg.Connection) -> int:
    """Mark runs that are still 'running' after STUCK_RUN_AFTER as failed.

    If the process is killed, its 'finally'/'except' code never runs, so its
    row would say 'running' forever. Only OLD rows are touched, so a run that
    another process started a minute ago is left alone.
    Returns how many rows were changed.
    """
    with conn.transaction():
        cursor = conn.execute(
            """
            UPDATE etl_runs
            SET finished_at = now(), status = 'failed',
                error_message = 'Still running at the start of a later run: '
                                'the process was probably killed or crashed'
            WHERE status = 'running' AND started_at < now() - %s::interval
            """,
            (STUCK_RUN_AFTER,),
        )
        return cursor.rowcount
