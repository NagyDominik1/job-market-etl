"""Pipeline runner: extract -> transform -> load -> enrich, logged in the etl_runs table.

Run it with:
    .venv\\Scripts\\python -m src.main
"""

import logging
import sys

import psycopg
import requests

from src.db import get_connection
from src.enrich import enrich
from src.extract import extract
from src.load import load_jobs
from src.transform import load_raw, transform

logger = logging.getLogger(__name__)

# Errors we expect can happen during a normal run (network down, database
# problem, missing file, bad data). For these a one-line message is enough.
# Any OTHER error is probably a bug, so we also log the full traceback.
EXPECTED_ERRORS = (requests.RequestException, psycopg.Error, OSError, ValueError, RuntimeError)


# --- Recording runs in etl_runs ---------------------------------------------
# Each function opens its OWN short connection. "with get_connection()" commits
# when the block ends, so the etl_runs row is saved immediately and does not
# depend on the data load. Even if the load is rolled back, the 'failed'
# record written here stays in the table.

def start_run() -> int:
    """Insert a 'running' row into etl_runs and return its id."""
    with get_connection() as conn:
        # RETURNING id gives us back the id the database generated.
        cursor = conn.execute("INSERT INTO etl_runs (status) VALUES ('running') RETURNING id")
        run_id = cursor.fetchone()[0]
    logger.info("Started ETL run id=%d", run_id)
    return run_id


def finish_run_success(run_id: int, rows_extracted: int, rows_loaded: int, rows_enriched: int | None) -> None:
    """Mark the run as successful and store the row counts."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE etl_runs
            SET finished_at = now(), status = 'success',
                rows_extracted = %s, rows_loaded = %s, rows_enriched = %s
            WHERE id = %s
            """,
            (rows_extracted, rows_loaded, rows_enriched, run_id),
        )


def finish_run_failed(run_id: int, error_message: str, rows_extracted: int | None) -> None:
    """Mark the run as failed and store the error message."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE etl_runs
            SET finished_at = now(), status = 'failed',
                rows_extracted = %s, error_message = %s
            WHERE id = %s
            """,
            (rows_extracted, error_message, run_id),
        )


# --- The pipeline -------------------------------------------------------------

def run_enrichment() -> int | None:
    """Run the enrich step. Never raises: returns the count, or None if it crashed.

    Enrichment is optional extra data. When it runs, the jobs are already
    loaded, so a problem here is only logged as a warning and does NOT mark
    the whole run as failed.
    """
    try:
        with get_connection() as conn:
            return enrich(conn)
    except Exception:
        logger.warning("AI enrichment failed; the data load was not affected", exc_info=True)
        return None


def run_pipeline() -> None:
    """Run extract -> transform -> load once and record the result in etl_runs."""
    # If we can't even write the 'running' row (e.g. the database is down),
    # there is nothing to record the failure in, so just log it and stop.
    try:
        run_id = start_run()
    except (psycopg.Error, RuntimeError) as error:
        logger.error("Could not start the ETL run. Is the database running? %s", error)
        sys.exit(1)

    rows_extracted = None  # stays None if the extract step fails
    try:
        # EXTRACT: download the jobs and save them to data/raw/
        raw_path = extract()

        # TRANSFORM: read that exact file back and clean it
        jobs = load_raw(raw_path)
        rows_extracted = len(jobs)
        df = transform(jobs)

        # LOAD: upsert into the jobs table (its own connection and transaction)
        with get_connection() as conn:
            counts = load_jobs(conn, df)

        rows_loaded = counts["inserted"] + counts["updated"]

        # ENRICH: classify new jobs with Claude (optional, never fails the run)
        rows_enriched = run_enrichment()

        finish_run_success(run_id, rows_extracted, rows_loaded, rows_enriched)
        logger.info(
            "ETL run id=%d succeeded: extracted=%d, loaded=%d (inserted=%d, updated=%d), enriched=%s",
            run_id, rows_extracted, rows_loaded, counts["inserted"], counts["updated"], rows_enriched,
        )

    # "except Exception" catches every normal error from any step, so the
    # run is always marked as failed instead of staying 'running' forever.
    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"
        if isinstance(error, EXPECTED_ERRORS):
            logger.error("ETL run id=%d failed: %s", run_id, error_message)
        else:
            # logger.exception also prints the traceback (where the error happened).
            logger.exception("ETL run id=%d failed with an unexpected error", run_id)
        try:
            finish_run_failed(run_id, error_message, rows_extracted)
        except Exception:
            logger.exception("Could not record the failure in etl_runs")
        sys.exit(1)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_pipeline()


if __name__ == "__main__":
    main()
