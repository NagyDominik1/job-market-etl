"""Tests for the manual CSV extractor and its external_id generation."""

import pytest

from src.extractors import get_enabled_extractors
from src.extractors.csv_extractor import DEFAULT_CSV_DIR, CsvExtractor, make_external_id

HEADER = "title,company,city,description,url,deadline\n"


def write_csv(directory, name, content, encoding="utf-8"):
    (directory / name).write_text(content, encoding=encoding, newline="")


# --- external_id -------------------------------------------------------------

def test_external_id_uses_the_url_when_present():
    a = make_external_id({"title": "A", "company": "X", "city": "Budapest", "url": "https://e.com/1"})
    b = make_external_id({"title": "Totally different", "company": "Y", "city": "", "url": "https://e.com/1"})
    assert a == b  # same URL = same posting, whatever the other columns say
    assert len(a) == 64  # SHA-256 as hex


def test_external_id_falls_back_to_title_company_city():
    a = make_external_id({"title": "Data Engineer", "company": "Acme", "city": "Novi Sad", "url": ""})
    b = make_external_id({"title": "  data   ENGINEER ", "company": "acme", "city": "novi sad", "url": "  "})
    assert a == b  # case and extra spaces don't matter


def test_external_id_differs_for_different_postings():
    base = {"title": "Data Engineer", "company": "Acme", "city": "Budapest", "url": ""}
    assert make_external_id(base) != make_external_id({**base, "city": "Szeged"})
    assert make_external_id(base) != make_external_id({**base, "url": "https://e.com/1"})


def test_external_id_parts_are_kept_apart():
    # Without a separator both would be "abc".
    a = make_external_id({"title": "ab", "company": "c", "city": "", "url": ""})
    b = make_external_id({"title": "a", "company": "bc", "city": "", "url": ""})
    assert a != b


# --- Reading files -----------------------------------------------------------

def test_rows_are_read_with_raw_payload(tmp_path):
    write_csv(tmp_path, "jobs.csv", HEADER + 'Data Engineer,Acme,Budapest,"Python, SQL",https://e.com/1,2026-11-01\n')
    postings = CsvExtractor(tmp_path).fetch()
    assert len(postings) == 1
    assert postings[0]["payload"] == {
        "title": "Data Engineer", "company": "Acme", "city": "Budapest",
        "description": "Python, SQL", "url": "https://e.com/1", "deadline": "2026-11-01",
    }
    assert postings[0]["external_id"] == make_external_id(postings[0]["payload"])


def test_all_csv_files_in_the_folder_are_read(tmp_path):
    write_csv(tmp_path, "a.csv", HEADER + "Job A,Acme,Budapest,,,\n")
    write_csv(tmp_path, "b.csv", HEADER + "Job B,Acme,Budapest,,,\n")
    write_csv(tmp_path, "notes.txt", "not a csv")
    titles = [p["payload"]["title"] for p in CsvExtractor(tmp_path).fetch()]
    assert titles == ["Job A", "Job B"]


def test_excel_style_file_with_semicolons_and_bom(tmp_path):
    # Excel with Hungarian settings: ";" separator and a BOM at the start.
    content = "title;company;city;description;url;deadline\nFejlesztő;Példa Kft.;Győr;;;\n"
    write_csv(tmp_path, "excel.csv", content, encoding="utf-8-sig")
    postings = CsvExtractor(tmp_path).fetch()
    assert postings[0]["payload"]["title"] == "Fejlesztő"
    assert postings[0]["payload"]["city"] == "Győr"


def test_columns_can_be_in_any_order(tmp_path):
    write_csv(tmp_path, "jobs.csv", "url,title,city,company,deadline,description\n,Job,Pécs,Acme,,\n")
    assert CsvExtractor(tmp_path).fetch()[0]["payload"]["company"] == "Acme"


def test_wrong_header_is_rejected(tmp_path):
    write_csv(tmp_path, "jobs.csv", "title,company,town\nJob,Acme,Budapest\n")
    with pytest.raises(ValueError, match="header"):
        CsvExtractor(tmp_path).fetch()


def test_empty_lines_and_rows_without_title_are_skipped(tmp_path):
    write_csv(tmp_path, "jobs.csv", HEADER + "Job,Acme,Budapest,,,\n,,,,,\n,Acme,Budapest,no title,,\n")
    assert len(CsvExtractor(tmp_path).fetch()) == 1


def test_missing_folder_gives_no_postings(tmp_path):
    assert CsvExtractor(tmp_path / "does-not-exist").fetch() == []


def test_example_file_is_valid():
    postings = CsvExtractor(DEFAULT_CSV_DIR).fetch()
    assert len(postings) >= 3
    assert len({p["external_id"] for p in postings}) == len(postings)  # all different


# --- SOURCES setting -----------------------------------------------------------

def test_sources_setting_selects_extractors(monkeypatch):
    monkeypatch.setenv("SOURCES", " CSV, arbeitnow,csv ,")
    assert [e.name for e in get_enabled_extractors()] == ["csv", "arbeitnow"]


def test_unknown_source_is_rejected(monkeypatch):
    monkeypatch.setenv("SOURCES", "arbeitnow,linkedin")
    with pytest.raises(ValueError, match="linkedin"):
        get_enabled_extractors()
