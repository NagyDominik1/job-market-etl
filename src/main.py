"""Pipeline runner (v2, Phase 1): run every enabled source and store its raw postings.

    1. apply pending database migrations (src/migrate.py)
    2. mark runs left 'running' by a killed process as failed
    3. for each source in SOURCES: fetch -> insert into raw_postings,
       logged as one etl_runs row per source

One failing source does NOT stop the others. The exit code is 1 if any source
failed, so a scheduler (cron, GitHub Actions) can see that something went wrong.

The v1 transform / load / enrich steps are not run for now: they are
reconnected to the new tables in Phase 2 and 3.

Run it with:
    .venv\\Scripts\\python -m src.main
"""

import logging
import sys

import psycopg
import requests

from src.db import get_connection
from src.extractors import Extractor, get_enabled_extractors
from src.migrate import apply_migrations
from src.raw_postings import register_source, save_raw_postings
from src.runs import finish_run_failed, finish_run_success, mark_stuck_runs_failed, start_run

logger = logging.getLogger(__name__)

# Errors we expect can happen during a normal run (network down, database
# problem, missing file, bad data). For these a one-line message is enough.
# Any OTHER error is probably a bug, so we also log the full traceback.
EXPECTED_ERRORS = (requests.RequestException, psycopg.Error, OSError, ValueError, RuntimeError)


def run_source(conn: psycopg.Connection, extractor: Extractor) -> bool:
    """Fetch one source and store its new postings. Returns True on success.

    Never raises: any error is logged and recorded in etl_runs, so the caller
    can simply go on with the next source.
    """
    try:
        source_id = register_source(conn, extractor)
        run_id = start_run(conn, "extract", source_id)
    except psycopg.Error as error:
        # Without a run row there is nowhere to record the failure.
        logger.error("Source %s: could not start the run: %s", extractor.name, error)
        return False

    rows_extracted = None  # stays None if fetch() fails
    try:
        postings = extractor.fetch()
        rows_extracted = len(postings)
        counts = save_raw_postings(conn, source_id, postings)
        # rows_loaded = NEW raw postings; rows_extracted - rows_loaded = already
        # seen + duplicates within this fetch.
        finish_run_success(conn, run_id, rows_extracted, counts["new"])
        logger.info(
            "Source %s (run id=%d): fetched=%d, new=%d, already seen=%d, duplicates in this fetch=%d",
            extractor.name, run_id, rows_extracted, counts["new"], counts["already_seen"], counts["duplicates"],
        )
        return True

    # "except Exception" catches every normal error, so the run is always
    # marked as failed instead of staying 'running' forever.
    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"
        if isinstance(error, EXPECTED_ERRORS):
            logger.error("Source %s (run id=%d) failed: %s", extractor.name, run_id, error_message)
        else:
            # logger.exception also prints the traceback (where the error happened).
            logger.exception("Source %s (run id=%d) failed with an unexpected error", extractor.name, run_id)
        try:
            finish_run_failed(conn, run_id, error_message, rows_extracted)
        except psycopg.Error:
            logger.exception("Could not record the failure in etl_runs")
        return False


def run_pipeline() -> None:
    """Run all enabled sources once. Exits with code 1 if anything failed."""
    try:
        # Read SOURCES first: a typo there should stop us before any work.
        extractors = get_enabled_extractors()
        # autocommit: each helper function commits its own transaction.
        conn = get_connection(autocommit=True)
    except EXPECTED_ERRORS as error:
        logger.error("Could not start the pipeline: %s", error)
        sys.exit(1)

    with conn:
        try:
            apply_migrations(conn)
            stuck = mark_stuck_runs_failed(conn)
            if stuck:
                logger.warning("Marked %d old run(s) stuck in 'running' as failed", stuck)
        except EXPECTED_ERRORS as error:
            logger.error("Could not prepare the database: %s", error)
            sys.exit(1)

        # Each source runs on its own; a failure is recorded and we go on.
        failed = [extractor.name for extractor in extractors if not run_source(conn, extractor)]

    if failed:
        logger.error("Finished with failed source(s): %s", ", ".join(failed))
        sys.exit(1)
    logger.info("Finished: all %d source(s) succeeded", len(extractors))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_pipeline()


if __name__ == "__main__":
    main()
