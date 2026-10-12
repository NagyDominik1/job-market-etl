"""Storing extractor results: the sources and raw_postings tables."""

import json
import logging

import psycopg

from src.extractors import Extractor, RawPosting

logger = logging.getLogger(__name__)

# Upsert all postings with ONE statement: unnest() turns the two arrays into
# rows (external_id, payload). One round trip to the database, instead of one
# per posting, matters once the database is remote (Supabase).
#
# ON CONFLICT ... DO UPDATE: a posting this source already delivered (same
# external_id) is not inserted again; we only note that we saw it again
# (last_seen_at) and keep its newest content (payload).
#
# RETURNING (xmax = 0): xmax is a hidden system column of every Postgres row.
# It is 0 for a row this statement INSERTED and non-zero for a row it UPDATED.
# So it tells us, per row, "new" vs "already seen", in the same single query.
UPSERT_RAW_POSTINGS_SQL = """
    INSERT INTO raw_postings (source_id, external_id, payload)
    SELECT %s, external_id, payload::jsonb
    FROM unnest(%s::text[], %s::text[]) AS t(external_id, payload)
    ON CONFLICT (source_id, external_id) DO UPDATE SET
        payload      = EXCLUDED.payload,
        last_seen_at = now()
    RETURNING (xmax = 0) AS inserted
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


def drop_duplicate_ids(postings: list[RawPosting]) -> list[RawPosting]:
    """Keep only the first posting for each external_id.

    Needed because "ON CONFLICT DO UPDATE" refuses to update the same row twice
    in one statement. (Arbeitnow's order shifts while we page through it, so
    the same job can appear on two pages of one download.)
    """
    seen = set()
    unique = []
    for posting in postings:
        if posting["external_id"] not in seen:
            seen.add(posting["external_id"])
            unique.append(posting)
    return unique


def save_raw_postings(conn: psycopg.Connection, source_id: int, postings: list[RawPosting]) -> dict:
    """Upsert the postings; return {"new", "already_seen", "duplicates"} counts.

    duplicates = postings that appeared more than once in THIS fetch.
    """
    unique = drop_duplicate_ids(postings)
    external_ids = [posting["external_id"] for posting in unique]
    # ensure_ascii=False keeps characters like "ő" readable in the database.
    payloads = [json.dumps(posting["payload"], ensure_ascii=False) for posting in unique]
    with conn.transaction():
        inserted_flags = [
            row[0] for row in conn.execute(UPSERT_RAW_POSTINGS_SQL, (source_id, external_ids, payloads))
        ]
    new = sum(inserted_flags)  # True counts as 1, False as 0
    return {
        "new": new,
        "already_seen": len(unique) - new,
        "duplicates": len(postings) - len(unique),
    }
