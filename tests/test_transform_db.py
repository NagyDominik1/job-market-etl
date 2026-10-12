"""Database tests for the transform step (src/transform.py): cross-source
dedup, merging rules, idempotent reruns and error isolation. They need
PostgreSQL and run in a temporary schema (db_conn fixture, conftest.py).
"""

from datetime import date
from types import SimpleNamespace

import pytest

from src.main import run_transform
from src.migrate import apply_migrations
from src.raw_postings import register_source, save_raw_postings
from src.transform import count_new_jobs, transform

# register_source() only reads .name and .kind, so a simple stand-in is enough.
ARBEITNOW = SimpleNamespace(name="arbeitnow", kind="api")
CSV = SimpleNamespace(name="csv", kind="csv")


def arbeitnow_posting(slug="erdsoft-data-engineer", **overrides):
    payload = {
        "slug": slug, "title": "Data Engineer (m/w/d)", "company_name": "ErdSoft GmbH",
        "location": "Subotica", "remote": False, "url": "https://arbeitnow.example/1",
        "description": "<p>Python</p>", "created_at": 1700000000,
    }
    payload.update(overrides)
    return {"external_id": slug, "payload": payload}


def csv_posting(external_id="row-1", **overrides):
    payload = {
        "title": "Data engineer", "company": "ErdSoft d.o.o.", "city": "Szabadka",
        "description": "", "url": "", "deadline": "2026-11-15",
    }
    payload.update(overrides)
    return {"external_id": external_id, "payload": payload}


@pytest.fixture
def conn(db_conn):
    apply_migrations(db_conn)
    return db_conn


@pytest.fixture
def sources(conn):
    return {"arbeitnow": register_source(conn, ARBEITNOW), "csv": register_source(conn, CSV)}


def count(conn, sql):
    return conn.execute(sql).fetchone()[0]


def test_same_job_from_two_sources_becomes_one_job(conn, sources):
    save_raw_postings(conn, sources["arbeitnow"], [arbeitnow_posting()])
    save_raw_postings(conn, sources["csv"], [csv_posting()])

    counts = transform(conn)

    assert (counts["new_jobs"], counts["merged"], counts["errors"]) == (1, 1, 0)
    assert count(conn, "SELECT count(*) FROM jobs") == 1
    assert count(conn, "SELECT count(*) FROM raw_postings") == 2
    assert count(conn, "SELECT count(DISTINCT job_id) FROM raw_postings WHERE job_id IS NOT NULL") == 1


def test_merge_fills_empty_fields_and_never_empties_them(conn, sources):
    # Arbeitnow first: has a url, no deadline. CSV second: deadline, no url.
    save_raw_postings(conn, sources["arbeitnow"], [arbeitnow_posting()])
    transform(conn)
    save_raw_postings(conn, sources["csv"], [csv_posting()])
    transform(conn)

    title, url, deadline, description = conn.execute(
        "SELECT title, url, deadline, description FROM jobs"
    ).fetchone()
    assert title == "Data Engineer"                   # the first source's spelling stays
    assert url == "https://arbeitnow.example/1"       # not replaced by the CSV's empty url
    assert deadline == date(2026, 11, 15)             # filled in from the CSV
    assert description == "Python"                    # not replaced by the CSV's empty description


def test_different_jobs_stay_apart(conn, sources):
    save_raw_postings(conn, sources["csv"], [
        csv_posting("a"), csv_posting("b", city="Novi Sad"), csv_posting("c", title="Data Analyst"),
    ])
    assert transform(conn)["new_jobs"] == 3


def test_rerun_is_idempotent(conn, sources):
    save_raw_postings(conn, sources["arbeitnow"], [arbeitnow_posting()])
    save_raw_postings(conn, sources["csv"], [csv_posting()])
    transform(conn)

    # Next pipeline run: the same postings are delivered again.
    save_raw_postings(conn, sources["arbeitnow"], [arbeitnow_posting()])
    save_raw_postings(conn, sources["csv"], [csv_posting()])
    counts = transform(conn)

    assert counts["processed"] == 0                   # nothing pending
    assert count(conn, "SELECT count(*) FROM jobs") == 1
    assert count(conn, "SELECT count(*) FROM raw_postings") == 2


def test_last_seen_at_follows_the_raw_postings(conn, sources):
    save_raw_postings(conn, sources["arbeitnow"], [arbeitnow_posting()])
    transform(conn)
    conn.execute("UPDATE jobs SET last_seen_at = now() - interval '3 days'")

    save_raw_postings(conn, sources["arbeitnow"], [arbeitnow_posting()])  # seen again now
    transform(conn)

    assert count(conn, "SELECT last_seen_at > now() - interval '1 minute' FROM jobs") is True
    assert count(conn, "SELECT first_seen_at < last_seen_at FROM jobs") is True


def test_bad_postings_are_skipped_and_do_not_stop_the_others(conn, sources):
    save_raw_postings(conn, sources["csv"], [
        csv_posting("bad-deadline", deadline="next Friday"),
        csv_posting("no-company", company=""),
        csv_posting("good"),
    ])
    save_raw_postings(conn, sources["arbeitnow"], [
        {"external_id": "broken", "payload": ["not", "an", "object"]},
    ])

    counts = transform(conn)

    assert (counts["processed"], counts["linked"], counts["errors"]) == (4, 1, 3)
    errors = dict(conn.execute(
        "SELECT external_id, transform_error FROM raw_postings WHERE transform_error IS NOT NULL"
    ).fetchall())
    assert set(errors) == {"bad-deadline", "no-company", "broken"}
    assert "not a date" in errors["bad-deadline"]
    assert "no company name" in errors["no-company"]


def test_fixed_posting_goes_through_on_the_next_run(conn, sources):
    save_raw_postings(conn, sources["csv"], [csv_posting("row", deadline="next Friday")])
    assert transform(conn)["errors"] == 1

    # I fix the CSV row; the next run delivers the corrected payload.
    save_raw_postings(conn, sources["csv"], [csv_posting("row", deadline="2026-12-01")])
    assert transform(conn)["linked"] == 1
    assert count(conn, "SELECT transform_error FROM raw_postings") is None


def test_new_jobs_are_counted_since_the_run_started(conn, sources):
    save_raw_postings(conn, sources["csv"], [csv_posting("old")])
    transform(conn)
    run_started_at = count(conn, "SELECT now()")

    save_raw_postings(conn, sources["csv"], [csv_posting("old"), csv_posting("new", title="Data Analyst")])
    transform(conn)

    assert count_new_jobs(conn, run_started_at) == 1


def test_transform_run_is_logged_with_its_errors(conn, sources):
    save_raw_postings(conn, sources["csv"], [csv_posting("good"), csv_posting("bad", company="")])

    assert run_transform(conn) is True

    row = conn.execute(
        "SELECT step, source_id, status, rows_extracted, rows_loaded, error_message FROM etl_runs"
    ).fetchone()
    assert row[:5] == ("transform", None, "success", 2, 1)
    assert row[5].startswith("1 raw posting(s) not transformed")
