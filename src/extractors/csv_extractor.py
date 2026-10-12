"""Manual CSV extractor: postings I copy by hand into data/manual/*.csv.

This is the legal way to include job sites that offer no API or email alert:
no scraping, I note the posting myself. Required header (any column order):
    title,company,city,description,url,deadline

CSV files have no ID column, so we compute one (see make_external_id):
the URL if there is one, otherwise title + company + city.

Settings (environment variable, optional):
    CSV_DIR  folder with the .csv files (default: data/manual)
"""

import csv
import hashlib
import logging
import os
from pathlib import Path

from src.extractors.base import Extractor, RawPosting

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CSV_DIR = PROJECT_ROOT / "data" / "manual"

EXPECTED_COLUMNS = ["title", "company", "city", "description", "url", "deadline"]


def normalize(value: str | None) -> str:
    """Lowercase, trim and collapse inner whitespace: "  Data  Engineer " -> "data engineer"."""
    return " ".join((value or "").split()).lower()


def make_external_id(row: dict) -> str:
    """Return a stable ID for a CSV row: a SHA-256 hash of its URL,
    or of title + company + city if the URL is empty.

    Hashing gives a fixed-length ID however long the URL is. The values are
    normalized first, so "Data Engineer" and "data  engineer " get the same ID,
    and the "url:" / "text:" prefixes keep the two kinds of keys apart.
    """
    url = (row.get("url") or "").strip()
    if url:
        key = "url:" + url
    else:
        # "|" between the parts, so ("ab", "c") and ("a", "bc") give different keys.
        key = "text:" + "|".join(normalize(row.get(column)) for column in ("title", "company", "city"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def read_csv_file(path: Path) -> list[RawPosting]:
    """Read one CSV file and return its rows as raw postings."""
    # utf-8-sig: also accepts the invisible "BOM" mark Excel puts at the start.
    # newline="": required by the csv module, so line breaks inside quoted
    # fields (e.g. a multi-line description) are read correctly.
    with open(path, encoding="utf-8-sig", newline="") as f:
        # Excel with Hungarian/Serbian regional settings saves CSV files with
        # ";" instead of ","; detect which one this file uses from its header.
        header_line = f.readline()
        f.seek(0)
        delimiter = ";" if header_line.count(";") > header_line.count(",") else ","
        reader = csv.DictReader(f, delimiter=delimiter)

        columns = [(name or "").strip().lower() for name in (reader.fieldnames or [])]
        if sorted(columns) != sorted(EXPECTED_COLUMNS):
            raise ValueError(
                f"{path.name}: the header must be {','.join(EXPECTED_COLUMNS)} (got {','.join(columns)})"
            )
        reader.fieldnames = columns  # use the cleaned names as dict keys

        postings: list[RawPosting] = []
        # start=2: line 1 is the header, so the first data row is line 2.
        for line_number, row in enumerate(reader, start=2):
            # Missing cells come back as None; turn them into "".
            # (A row with too MANY cells puts the extras under the key None; drop those.)
            row = {column: (row.get(column) or "") for column in EXPECTED_COLUMNS}
            if not any(value.strip() for value in row.values()):
                continue  # completely empty line
            if not row["title"].strip():
                logger.warning("%s line %d: skipped, the title is empty", path.name, line_number)
                continue
            postings.append({"external_id": make_external_id(row), "payload": row})

    logger.info("Read %d postings from %s", len(postings), path.name)
    return postings


class CsvExtractor(Extractor):
    name = "csv"
    kind = "csv"

    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory or os.environ.get("CSV_DIR") or DEFAULT_CSV_DIR)

    def fetch(self) -> list[RawPosting]:
        if not self.directory.is_dir():
            # Not an error: maybe I simply have no manual postings.
            logger.warning("CSV folder %s does not exist, no manual postings", self.directory)
            return []

        postings: list[RawPosting] = []
        for path in sorted(self.directory.glob("*.csv")):
            postings.extend(read_csv_file(path))
        logger.info("CSV extract: %d postings from %s", len(postings), self.directory)
        return postings
