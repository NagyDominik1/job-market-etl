"""LOAD step: write the cleaned jobs DataFrame into the `jobs` table.

Uses an "upsert": new slugs are inserted, slugs that already exist are updated.
"""

import logging

import numpy as np
import pandas as pd
import psycopg

logger = logging.getLogger(__name__)

# The data columns we write, in the same order as the %s placeholders below.
# (first_seen_at / last_seen_at are handled by the database.)
JOB_COLUMNS = [
    "slug", "title", "company_name", "location", "remote",
    "tags", "job_types", "url", "description", "posted_at",
]

# One %s placeholder per column. psycopg sends the values separately from the
# SQL text, so a value like "O'Reilly" can never break (or inject into) the query.
UPSERT_SQL = """
    INSERT INTO jobs (
        slug, title, company_name, location, remote,
        tags, job_types, url, description, posted_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (slug) DO UPDATE SET
        title        = EXCLUDED.title,
        company_name = EXCLUDED.company_name,
        location     = EXCLUDED.location,
        remote       = EXCLUDED.remote,
        tags         = EXCLUDED.tags,
        job_types    = EXCLUDED.job_types,
        url          = EXCLUDED.url,
        description  = EXCLUDED.description,
        posted_at    = EXCLUDED.posted_at,
        last_seen_at = now()
"""

COUNT_SQL = "SELECT count(*) FROM jobs"


def to_python_value(value):
    """Convert one pandas/numpy value into a plain Python value psycopg understands."""
    if isinstance(value, list):
        return value  # lists stay lists; psycopg turns them into TEXT[] arrays
    if value is None or pd.isna(value):
        return None  # NaN / NaT / None -> SQL NULL
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()  # pandas Timestamp -> Python datetime
    if isinstance(value, np.generic):
        return value.item()  # e.g. numpy.bool_ -> bool, numpy.int64 -> int
    return value


def dataframe_to_rows(df: pd.DataFrame) -> list[tuple]:
    """Turn the DataFrame into a list of tuples, one per row, in JOB_COLUMNS order."""
    rows = []
    # to_dict("records") gives one dict per row, e.g. {"slug": "...", "title": "...", ...}
    for record in df.to_dict(orient="records"):
        rows.append(tuple(to_python_value(record[column]) for column in JOB_COLUMNS))
    return rows


def count_jobs(cursor: psycopg.Cursor) -> int:
    """Return the number of rows currently in the jobs table."""
    cursor.execute(COUNT_SQL)
    return cursor.fetchone()[0]


def load_jobs(conn: psycopg.Connection, df: pd.DataFrame) -> dict:
    """Upsert all jobs in ONE transaction and return {"rows_in", "inserted", "updated"}.

    If anything fails, the whole transaction is rolled back, so the table is
    never left half-loaded.
    """
    rows = dataframe_to_rows(df)

    # conn.transaction() starts a transaction. At the end of the "with" block
    # it COMMITs; if an exception happens inside, it ROLLs BACK instead and
    # re-raises the exception.
    with conn.transaction():
        with conn.cursor() as cursor:
            rows_before = count_jobs(cursor)
            # executemany runs the same query once for every tuple in rows.
            cursor.executemany(UPSERT_SQL, rows)
            rows_after = count_jobs(cursor)

    # Every new slug adds one row to the table; every other row was an update.
    # (This assumes no other program writes to `jobs` at the same time.)
    inserted = rows_after - rows_before
    counts = {
        "rows_in": len(rows),
        "inserted": inserted,
        "updated": len(rows) - inserted,
    }
    logger.info(
        "Load: rows in=%d, inserted=%d, updated=%d (jobs table: %d -> %d rows)",
        counts["rows_in"], counts["inserted"], counts["updated"], rows_before, rows_after,
    )
    return counts
