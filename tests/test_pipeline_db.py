"""Database tests for the Phase 1 pipeline: storing raw postings, the etl_runs
log and source isolation. They need PostgreSQL and run in a temporary schema
(see the db_conn fixture in conftest.py); they are skipped without a database.
"""

import pytest

from src.extractors import Extractor
from src.main import run_source
from src.migrate import apply_migrations
from src.raw_postings import register_source, save_raw_postings
from src.runs import mark_stuck_runs_failed, start_run


class FakeExtractor(Extractor):
    name = "fake"
    kind = "api"

    def __init__(self, postings=None, error=None):
        self.postings = postings or []
        self.error = error

    def fetch(self):
        if self.error:
            raise self.error
        return self.postings


class OtherFakeExtractor(FakeExtractor):
    name = "other-fake"


def posting(external_id, title="Job"):
    return {"external_id": external_id, "payload": {"title": title}}


@pytest.fixture
def conn(db_conn):
    apply_migrations(db_conn)  # the real v2 schema, in the temporary schema
    return db_conn


def test_register_source_returns_the_same_id_every_time(conn):
    first = register_source(conn, FakeExtractor())
    assert register_source(conn, FakeExtractor()) == first
    assert register_source(conn, OtherFakeExtractor()) != first


def test_only_new_postings_are_inserted(conn):
    source_id = register_source(conn, FakeExtractor())
    assert save_raw_postings(conn, source_id, [posting("a"), posting("b")]) == 2
    # Second run: "a" and "b" were already seen, only "c" is new.
    assert save_raw_postings(conn, source_id, [posting("a"), posting("b"), posting("c")]) == 1
    assert conn.execute("SELECT count(*) FROM raw_postings").fetchone()[0] == 3


def test_duplicates_within_one_batch_are_stored_once(conn):
    source_id = register_source(conn, FakeExtractor())
    assert save_raw_postings(conn, source_id, [posting("a"), posting("a")]) == 1


def test_same_external_id_from_another_source_is_new(conn):
    first = register_source(conn, FakeExtractor())
    second = register_source(conn, OtherFakeExtractor())
    save_raw_postings(conn, first, [posting("a")])
    assert save_raw_postings(conn, second, [posting("a")]) == 1


def test_payload_is_stored_as_jsonb(conn):
    source_id = register_source(conn, FakeExtractor())
    save_raw_postings(conn, source_id, [posting("a", title="Fejlesztő")])
    # ->> reads a field from the JSONB as text.
    assert conn.execute("SELECT payload->>'title' FROM raw_postings").fetchone()[0] == "Fejlesztő"


def test_empty_result_is_fine(conn):
    source_id = register_source(conn, FakeExtractor())
    assert save_raw_postings(conn, source_id, []) == 0


def test_only_old_running_runs_are_marked_failed(conn):
    old_run = start_run(conn, None)
    new_run = start_run(conn, None)
    conn.execute("UPDATE etl_runs SET started_at = now() - interval '2 hours' WHERE id = %s", (old_run,))

    assert mark_stuck_runs_failed(conn) == 1
    statuses = dict(conn.execute("SELECT id, status FROM etl_runs").fetchall())
    assert statuses == {old_run: "failed", new_run: "running"}


def test_failing_source_is_recorded_and_does_not_stop_the_next_one(conn):
    broken = FakeExtractor(error=ValueError("API is down"))
    working = OtherFakeExtractor(postings=[posting("a"), posting("b")])

    assert run_source(conn, broken) is False
    assert run_source(conn, working) is True

    rows = conn.execute(
        """
        SELECT s.name, r.status, r.rows_extracted, r.rows_loaded, r.error_message
        FROM etl_runs r JOIN sources s ON s.id = r.source_id
        ORDER BY r.id
        """
    ).fetchall()
    assert rows == [
        ("fake", "failed", None, None, "ValueError: API is down"),
        ("other-fake", "success", 2, 2, None),
    ]
