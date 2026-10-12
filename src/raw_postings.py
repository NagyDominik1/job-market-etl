"""Storing extractor results: the sources and raw_postings tables."""

import json

import psycopg

from src.extractors import Extractor, RawPosting

# Insert all postings with ONE statement: unnest() turns the two arrays into
# rows (external_id, payload). One round trip to the database, instead of one
# per posting, matters once the database is remote (Supabase).
# ON CONFLICT DO NOTHING: a posting this source already delivered earlier
# (same external_id) is skipped, so the table never has duplicates and
# the rows that WERE inserted are exactly the new postings.
INSERT_RAW_POSTINGS_SQL = """
    INSERT INTO raw_postings (source_id, external_id, payload)
    SELECT %s, external_id, payload::jsonb
    FROM unnest(%s::text[], %s::text[]) AS t(external_id, payload)
    ON CONFLICT (source_id, external_id) DO NOTHING
"""


def register_source(conn: psycopg.Connection, extractor: Extractor) -> int:
    """Make sure the extractor has a row in `sources` and return its id."""
    with conn.transaction():
        # DO UPDATE (not DO NOTHING) so that RETURNING also gives the id of an
        # existing row, and a changed `kind` in the code is saved too.
        return conn.execute(
            """
            INSERT INTO sources (name, kind) VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET kind = EXCLUDED.kind
            RETURNING id
            """,
            (extractor.name, extractor.kind),
        ).fetchone()[0]


def save_raw_postings(conn: psycopg.Connection, source_id: int, postings: list[RawPosting]) -> int:
    """Insert the postings that are new for this source; return how many were new."""
    external_ids = [posting["external_id"] for posting in postings]
    # ensure_ascii=False keeps characters like "ő" readable in the database.
    payloads = [json.dumps(posting["payload"], ensure_ascii=False) for posting in postings]
    with conn.transaction():
        cursor = conn.execute(INSERT_RAW_POSTINGS_SQL, (source_id, external_ids, payloads))
        # For an INSERT, rowcount = number of rows actually inserted (skipped
        # conflicts don't count).
        return cursor.rowcount
