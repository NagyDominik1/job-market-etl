"""Tests for src/transform.py. Run with:  .venv\\Scripts\\python -m pytest

All tests use small hand-made data: no network, no files.
"""

import pandas as pd

from src.transform import FINAL_COLUMNS, clean_description, clean_title, transform


def make_job(**overrides) -> dict:
    """Return a valid raw job dict. Pass keyword arguments to change fields."""
    job = {
        "slug": "python-developer-berlin-1",
        "company_name": "Example GmbH",
        "title": "Python Developer",
        "description": "<p>Hello</p>",
        "remote": False,
        "url": "https://example.com/jobs/1",
        "tags": ["Python"],
        "job_types": ["full time"],
        "location": "Berlin",
        "created_at": 1700000000,
    }
    job.update(overrides)
    return job


# --- clean_description ------------------------------------------------------

def test_description_html_is_removed_and_entities_unescaped():
    html = "<p>Tom &amp; Jerry&nbsp;&nbsp;GmbH</p><ul><li>Python &lt;3</li><li>SQL</li></ul>"
    assert clean_description(html) == "Tom & Jerry GmbH\nPython <3\nSQL"


def test_description_inline_tags_do_not_split_sentences():
    html = "<p>We use <strong>Python</strong> and <a href='#'>SQL</a> daily.</p>"
    assert clean_description(html) == "We use Python and SQL daily."


def test_description_double_escaped_html_is_cleaned():
    # Some API descriptions are HTML that was escaped a second time.
    html = "&lt;p&gt;Hello&lt;/p&gt;&lt;p&gt;World&lt;/p&gt;"
    assert clean_description(html) == "Hello\nWorld"


def test_description_blank_lines_are_collapsed():
    html = "<p>First</p><p>   </p><br><br><p>Second</p>"
    assert clean_description(html) == "First\nSecond"


def test_description_empty_becomes_none():
    assert clean_description("<p>  </p>") is None
    assert clean_description(None) is None


# --- clean_title --------------------------------------------------------------

def test_gender_markers_are_removed_from_titles():
    assert clean_title("Python Developer (m/w/d)") == "Python Developer"
    assert clean_title("Python Developer (M/F/D)") == "Python Developer"
    assert clean_title("(w/m/d) Python Developer") == "Python Developer"
    assert clean_title("Python Developer (f/m/d) - Berlin") == "Python Developer - Berlin"
    assert clean_title("  Python Developer ( m / w / x )  ") == "Python Developer"


def test_title_keeps_other_brackets():
    assert clean_title("Senior Developer (Backend)") == "Senior Developer (Backend)"


def test_title_with_only_marker_becomes_none():
    assert clean_title(" (m/w/d) ") is None


# --- transform ------------------------------------------------------------------

def test_unix_timestamp_is_converted_to_utc_datetime():
    df = transform([make_job(created_at=1700000000)])
    assert df.loc[0, "posted_at"] == pd.Timestamp("2023-11-14 22:13:20", tz="UTC")


def test_duplicate_slugs_are_removed_keeping_first():
    jobs = [
        make_job(slug="a", title="First"),
        make_job(slug="a", title="Second"),
        make_job(slug="b", title="Other"),
    ]
    df = transform(jobs)
    assert list(df["slug"]) == ["a", "b"]
    assert df.loc[0, "title"] == "First"


def test_rows_without_slug_or_title_are_dropped():
    jobs = [
        make_job(slug="ok"),
        make_job(slug=None),
        make_job(slug="   "),  # only whitespace counts as missing too
        make_job(slug="no-title", title=""),
    ]
    df = transform(jobs)
    assert list(df["slug"]) == ["ok"]


def test_missing_tags_become_empty_list():
    job_without_tags = make_job(slug="a")
    del job_without_tags["tags"]
    jobs = [job_without_tags, make_job(slug="b", tags=None, job_types=None)]
    df = transform(jobs)
    assert df.loc[0, "tags"] == []
    assert df.loc[1, "tags"] == []
    assert df.loc[1, "job_types"] == []


def test_empty_strings_become_none_and_text_is_stripped():
    df = transform([make_job(company_name="  Example GmbH  ", location="")])
    assert df.loc[0, "company_name"] == "Example GmbH"
    assert df.loc[0, "location"] is None


def test_output_columns_match_jobs_table():
    df = transform([make_job(extra_field="ignored")])
    assert list(df.columns) == FINAL_COLUMNS
