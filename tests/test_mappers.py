"""Tests for the source mappers (src/mappers/). Includes the v1 transform()
tests, ported: timestamps, empty values and the output columns.
"""

from dataclasses import fields
from datetime import date, datetime, timezone

import pytest

from src.extractors import EXTRACTORS
from src.mappers import MAPPERS, NormalizedJob, get_mapper
from src.mappers.arbeitnow import map_arbeitnow
from src.mappers.csv_mapper import map_csv, parse_deadline

JOBS_TABLE_DATA_COLUMNS = [
    "title", "company_name", "location", "remote", "url", "description", "posted_at", "deadline",
]


def arbeitnow_payload(**overrides) -> dict:
    payload = {
        "slug": "python-developer-berlin-1",
        "company_name": "Example GmbH",
        "title": "Python Developer (m/w/d)",
        "description": "<p>Hello &amp; welcome</p>",
        "remote": False,
        "url": "https://example.com/jobs/1",
        "tags": ["Python"],
        "job_types": ["full time"],
        "location": "Berlin",
        "created_at": 1700000000,
    }
    payload.update(overrides)
    return payload


def csv_payload(**overrides) -> dict:
    payload = {
        "title": "Data Engineer", "company": "ErdSoft d.o.o.", "city": "Szabadka",
        "description": "Python, SQL", "url": "https://example.com/1", "deadline": "2026-11-15",
    }
    payload.update(overrides)
    return payload


def test_every_source_has_a_mapper():
    assert set(MAPPERS) == set(EXTRACTORS)


def test_unknown_source_has_no_mapper():
    with pytest.raises(ValueError, match="no mapper"):
        get_mapper("linkedin")


def test_normalized_job_fields_match_the_jobs_table():
    assert [field.name for field in fields(NormalizedJob)] == JOBS_TABLE_DATA_COLUMNS


# --- Arbeitnow ---------------------------------------------------------------------

def test_arbeitnow_payload_is_cleaned():
    job = map_arbeitnow(arbeitnow_payload())
    assert job == NormalizedJob(
        title="Python Developer",
        company_name="Example GmbH",
        location="Berlin",
        remote=False,
        url="https://example.com/jobs/1",
        description="Hello & welcome",
        posted_at=datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc),
        deadline=None,
    )


def test_arbeitnow_unix_timestamp_is_utc_datetime():
    assert map_arbeitnow(arbeitnow_payload(created_at=0)).posted_at == datetime(1970, 1, 1, tzinfo=timezone.utc)


def test_arbeitnow_bad_or_missing_values_become_none():
    job = map_arbeitnow(arbeitnow_payload(
        created_at="yesterday", remote="yes", location="", company_name="  ", url=None,
    ))
    assert (job.posted_at, job.remote, job.location, job.company_name, job.url) == (None, None, None, None, None)


def test_arbeitnow_missing_fields_are_none():
    job = map_arbeitnow({"title": "Only a title"})
    assert job.title == "Only a title"
    assert job.company_name is None and job.posted_at is None


def test_arbeitnow_non_object_payload_is_rejected():
    with pytest.raises(ValueError):
        map_arbeitnow(["not", "a", "dict"])


# --- CSV ---------------------------------------------------------------------------

def test_csv_payload_is_cleaned():
    job = map_csv(csv_payload(title="  Data Engineer (m/ž) "))
    assert job == NormalizedJob(
        title="Data Engineer",
        company_name="ErdSoft d.o.o.",
        location="Szabadka",
        remote=None,
        url="https://example.com/1",
        description="Python, SQL",
        posted_at=None,
        deadline=date(2026, 11, 15),
    )


def test_csv_empty_cells_become_none():
    job = map_csv(csv_payload(url="", deadline="", description="  "))
    assert (job.url, job.deadline, job.description) == (None, None, None)


def test_csv_remote_city_means_remote():
    assert map_csv(csv_payload(city="Távmunka")).remote is True
    assert map_csv(csv_payload(city="Novi Sad")).remote is None


@pytest.mark.parametrize("text", ["2026-11-15", "2026.11.15.", "2026.11.15", "15.11.2026.", "15.11.2026", "2026. 11. 15."])
def test_deadline_formats(text):
    assert parse_deadline(text) == date(2026, 11, 15)


def test_bad_deadline_is_an_error():
    with pytest.raises(ValueError, match="not a date"):
        map_csv(csv_payload(deadline="next Friday"))
