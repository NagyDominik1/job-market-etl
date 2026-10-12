"""Pipeline runner (v2): extract, transform, enrich + score, report the new jobs.

    1. apply pending database migrations (src/migrate.py)
    2. mark runs left 'running' by a killed process as failed
    3. EXTRACT: for each source in SOURCES: fetch -> upsert into raw_postings,
       logged as one etl_runs row per source (step 'extract')
    4. TRANSFORM: pending raw postings -> jobs (src/transform.py),
       one etl_runs row (step 'transform')
    5. ENRICH: rules / Claude extraction + match score for every job
       (src/enrich.py, src/scoring.py), one etl_runs row (step 'enrich')
    6. log how many jobs are new in this run, and the top 10 new ones by score

One failing source does NOT stop the others, and the later steps still run
on whatever was extracted. The exit code is 1 if any step failed, so a
scheduler (cron, GitHub Actions) can see that something went wrong.

Run it with:
    .venv\\Scripts\\python -m src.main
"""

import logging
import sys

import psycopg
import requests

from src.db import get_connection
from src.enrich import enrich
from src.extractors import Extractor, get_enabled_extractors
from src.migrate import apply_migrations
from src.raw_postings import register_source, save_raw_postings
from src.runs import finish_run_failed, finish_run_success, mark_stuck_runs_failed, start_run
from src.transform import count_new_jobs, transform
from src.user_profile import load_profile

TOP_NEW_JOBS = 10

# The best new jobs of this run, for the log at the end.
TOP_NEW_JOBS_SQL = """
    SELECT e.match_score, j.title, j.company_name, j.location, e.match_reasons
    FROM jobs AS j
    JOIN job_enrichment AS e ON e.job_id = j.id
    WHERE j.first_seen_at >= %s
    ORDER BY e.match_score DESC NULLS LAST, j.id
    LIMIT %s
"""

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


def run_transform(conn: psycopg.Connection) -> bool:
    """Run the transform step as its own etl_runs row. Returns True on success.

    Single postings that fail don't fail the step: they get a transform_error,
    and their number is noted in the run's error_message.
    """
    try:
        run_id = start_run(conn, "transform")
    except psycopg.Error as error:
        logger.error("Could not start the transform run: %s", error)
        return False

    try:
        counts = transform(conn)
        message = None
        if counts["errors"]:
            message = f"{counts['errors']} raw posting(s) not transformed, see raw_postings.transform_error"
        # rows_extracted = raw postings processed, rows_loaded = linked to a job.
        finish_run_success(conn, run_id, counts["processed"], counts["linked"], message)
        return True
    except Exception as error:
        logger.exception("Transform (run id=%d) failed", run_id)
        try:
            finish_run_failed(conn, run_id, f"{type(error).__name__}: {error}", None)
        except psycopg.Error:
            logger.exception("Could not record the failure in etl_runs")
        return False


def run_enrich(conn: psycopg.Connection) -> bool:
    """Run the enrich step (extraction + match score) as its own etl_runs row.

    A failed Claude call doesn't fail the step (the job keeps its rule-based
    result); a broken profile file or a database error does.
    """
    try:
        run_id = start_run(conn, "enrich")
    except psycopg.Error as error:
        logger.error("Could not start the enrich run: %s", error)
        return False

    try:
        profile = load_profile()
        counts = enrich(conn, profile)
        message = (
            f"rules changed {counts['rules']}, Claude {counts['claude']} "
            f"({counts['claude_failed']} failed), scored {counts['scored']}"
        )
        # rows_extracted = jobs scored (all enriched jobs), rows_enriched = rows
        # written by the rules or Claude in this run.
        finish_run_success(
            conn, run_id, counts["scored"], counts["scored"], message,
            rows_enriched=counts["rules"] + counts["claude"],
        )
        return True
    except Exception as error:
        if isinstance(error, EXPECTED_ERRORS):
            logger.error("Enrich (run id=%d) failed: %s", run_id, error)
        else:
            logger.exception("Enrich (run id=%d) failed with an unexpected error", run_id)
        try:
            finish_run_failed(conn, run_id, f"{type(error).__name__}: {error}", None)
        except psycopg.Error:
            logger.exception("Could not record the failure in etl_runs")
        return False


def log_new_jobs(conn: psycopg.Connection, run_started_at) -> None:
    """Log how many jobs are new in this run, and the best ones with their reasons."""
    # "New" = first seen during THIS run (since it started).
    logger.info("New jobs in this run: %d", count_new_jobs(conn, run_started_at))
    rows = conn.execute(TOP_NEW_JOBS_SQL, (run_started_at, TOP_NEW_JOBS)).fetchall()
    if not rows:
        return
    lines = [f"Top {len(rows)} new jobs by match score:"]
    for score, title, company, location, reasons in rows:
        lines.append(f"  {score if score is not None else '-':>3}  {title} | {company or '?'} | {location or '?'}")
        lines.append(f"       {', '.join(reasons)}")
    logger.info("\n".join(lines))


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

        # The database's clock, so it compares correctly with jobs.first_seen_at.
        run_started_at = conn.execute("SELECT now()").fetchone()[0]

        # Each source runs on its own; a failure is recorded and we go on.
        failed = [extractor.name for extractor in extractors if not run_source(conn, extractor)]

        if not run_transform(conn):
            failed.append("transform")

        if not run_enrich(conn):
            failed.append("enrich")

        try:
            log_new_jobs(conn, run_started_at)
        except psycopg.Error as error:
            logger.error("Could not report the new jobs: %s", error)
            failed.append("new-jobs report")

    if failed:
        logger.error("Finished with failed step(s): %s", ", ".join(failed))
        sys.exit(1)
    logger.info("Finished: all %d source(s), transform and enrich succeeded", len(extractors))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_pipeline()


if __name__ == "__main__":
    main()
