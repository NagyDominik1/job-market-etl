"""TRANSFORM step (v2): raw_postings -> deduplicated jobs.

For every raw posting that has no job yet (raw_postings.job_id IS NULL):
    1. map      the source's payload -> NormalizedJob   (src/mappers/)
    2. check    title and company are present (needed for the fingerprint)
    3. upsert   into jobs by fingerprint                (src/normalize.py)
    4. link     raw_postings.job_id = that job
Finally, jobs.last_seen_at is updated from their raw postings.

Each posting is handled in its OWN transaction. A posting that fails gets a
transform_error and is skipped; the others continue. Failed postings are tried
again on every run, so after fixing a mapper (or a CSV row) they go through.

Run it on its own with:
    .venv\\Scripts\\python -m src.transform
"""

import logging
import sys
from dataclasses import asdict
from datetime import datetime

import psycopg

from src.db import get_connection
from src.mappers import get_mapper
from src.normalize import make_fingerprint

logger = logging.getLogger(__name__)

# Oldest first, so a newer source can fill in what an older one left empty.
SELECT_PENDING_SQL = """
    SELECT r.id, s.name, r.payload
    FROM raw_postings AS r
    JOIN sources AS s ON s.id = r.source_id
    WHERE r.job_id IS NULL
    ORDER BY r.fetched_at, r.id
"""

# Insert a new job, or merge into the job with the same fingerprint:
#   - title is never changed (the first source's spelling stays),
#   - COALESCE(old, new) = keep the existing value, but fill it in if it was
#     empty (NULL). A newer posting can add a missing deadline or url; it can
#     never replace a value with an empty one,
#   - posted_at: LEAST keeps the earliest date (LEAST ignores NULLs in Postgres),
#   - first_seen_at is not touched, so a job keeps its "first seen" date.
# RETURNING (xmax = 0): True if this statement inserted the row (a new job).
UPSERT_JOB_SQL = """
    INSERT INTO jobs (
        fingerprint, title, company_name, location, remote,
        url, description, posted_at, deadline
    )
    VALUES (
        %(fingerprint)s, %(title)s, %(company_name)s, %(location)s, %(remote)s,
        %(url)s, %(description)s, %(posted_at)s, %(deadline)s
    )
    ON CONFLICT (fingerprint) DO UPDATE SET
        company_name = COALESCE(jobs.company_name, EXCLUDED.company_name),
        location     = COALESCE(jobs.location,     EXCLUDED.location),
        remote       = COALESCE(jobs.remote,       EXCLUDED.remote),
        url          = COALESCE(jobs.url,          EXCLUDED.url),
        description  = COALESCE(jobs.description,  EXCLUDED.description),
        deadline     = COALESCE(jobs.deadline,     EXCLUDED.deadline),
        posted_at    = LEAST(jobs.posted_at, EXCLUDED.posted_at)
    RETURNING id, (xmax = 0) AS inserted
"""

LINK_SQL = "UPDATE raw_postings SET job_id = %s, transform_error = NULL WHERE id = %s"
ERROR_SQL = "UPDATE raw_postings SET transform_error = %s WHERE id = %s"

# A job was "last seen" when any of its raw postings was last seen.
# Only moves forward: "> j.last_seen_at" never makes the date older.
UPDATE_LAST_SEEN_SQL = """
    UPDATE jobs AS j
    SET last_seen_at = seen.last_seen_at
    FROM (
        SELECT job_id, max(last_seen_at) AS last_seen_at
        FROM raw_postings
        WHERE job_id IS NOT NULL
        GROUP BY job_id
    ) AS seen
    WHERE j.id = seen.job_id AND seen.last_seen_at > j.last_seen_at
"""


def transform_one(conn: psycopg.Connection, source_name: str, payload) -> tuple[int, bool]:
    """Map, check and upsert one posting. Returns (job id, True if the job is new).

    Raises an exception if the posting can't be turned into a job.
    """
    job = get_mapper(source_name)(payload)
    if not job.title:
        raise ValueError("no title")
    if not job.company_name:
        raise ValueError("no company name (needed to recognise the job across sources)")

    row = asdict(job)  # dataclass -> {"title": ..., "company_name": ..., ...}
    row["fingerprint"] = make_fingerprint(job.company_name, job.title, job.location, job.remote)
    job_id, inserted = conn.execute(UPSERT_JOB_SQL, row).fetchone()
    return job_id, inserted


def transform(conn: psycopg.Connection) -> dict:
    """Transform all pending raw postings; return the counts.

    `conn` must be in autocommit mode, so each posting's transaction is real.
    """
    pending = conn.execute(SELECT_PENDING_SQL).fetchall()
    counts = {"processed": len(pending), "linked": 0, "new_jobs": 0, "merged": 0, "errors": 0}

    for raw_id, source_name, payload in pending:
        try:
            # Upsert + link in ONE transaction: either both happen or neither.
            with conn.transaction():
                job_id, inserted = transform_one(conn, source_name, payload)
                conn.execute(LINK_SQL, (job_id, raw_id))
        # Any error (bad payload, a bug in a mapper, a rejected value) only
        # affects this posting: note why, and go on with the next one.
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            logger.warning("Raw posting %d (%s) not transformed: %s", raw_id, source_name, message)
            # If even this fails (e.g. the database is gone), the exception
            # propagates and the whole transform step is marked as failed.
            with conn.transaction():
                conn.execute(ERROR_SQL, (message, raw_id))
            counts["errors"] += 1
            continue

        counts["linked"] += 1
        counts["new_jobs" if inserted else "merged"] += 1

    with conn.transaction():
        counts["last_seen_updated"] = conn.execute(UPDATE_LAST_SEEN_SQL).rowcount

    logger.info(
        "Transform: processed=%d, new jobs=%d, merged into existing jobs=%d, errors=%d, "
        "jobs with a newer last_seen_at=%d",
        counts["processed"], counts["new_jobs"], counts["merged"], counts["errors"],
        counts["last_seen_updated"],
    )
    return counts


def count_new_jobs(conn: psycopg.Connection, since: datetime) -> int:
    """Number of jobs first seen at or after `since` (e.g. the start of this run)."""
    return conn.execute("SELECT count(*) FROM jobs WHERE first_seen_at >= %s", (since,)).fetchone()[0]


def main() -> None:
    """Run only the transform step (useful while working on mappers)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        with get_connection(autocommit=True) as conn:
            transform(conn)
    except (psycopg.Error, RuntimeError) as error:
        logger.error("Transform failed: %s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
