"""TRANSFORM step: clean the raw Arbeitnow jobs with pandas.

Turns the raw list of job dicts (saved by src/extract.py) into a clean
DataFrame whose columns match the `jobs` table in sql/init.sql.

Run it on its own with:
    .venv\\Scripts\\python -m src.transform                 (newest file in data/raw/)
    .venv\\Scripts\\python -m src.transform path\\to\\file.json
"""

import json
import logging
import re
import sys
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"

# Fields we keep from the API. Everything else is ignored.
RAW_COLUMNS = [
    "slug", "title", "company_name", "location", "remote",
    "tags", "job_types", "url", "description", "created_at",
]

# Final columns, in the same order as the `jobs` table
# (first_seen_at / last_seen_at are filled in by the database).
FINAL_COLUMNS = [
    "slug", "title", "company_name", "location", "remote",
    "tags", "job_types", "url", "description", "posted_at",
]

# Simple text columns that only need whitespace stripping.
# (title and description have their own cleaning functions.)
PLAIN_TEXT_COLUMNS = ["slug", "company_name", "location", "url"]

# Inline HTML tags that sit INSIDE a sentence, e.g. "We use <strong>Python</strong>".
# We remove these tags (but keep their text) before extracting text, otherwise
# the newline separator would split the sentence into three lines.
INLINE_TAGS = ["a", "abbr", "b", "code", "em", "font", "i", "small", "span", "strong", "sub", "sup", "u"]

# Gender markers that job titles in Germany, France etc. often contain.
#
# 1. In brackets: (m/w/d), (f/m/x), (h/f/n) (French: homme/femme/non-binaire),
#    (all genders), (alle Geschlechter), (gn) (German "geschlechtsneutral").
#      [mwfdxhn]            one of these letters, in any order
#      (?:/[mwfdxhn]){1,2}  followed by one or two more "/letter" parts
# 2. Without brackets, e.g. "Security Lead - m/f/d" or "Chargé RH H/F/X".
#    Here the letters must stand alone (not part of a word like "UX/UI").
# re.IGNORECASE makes it also match (M/W/D) or (All Genders).
LETTER_MARKER = r"[mwfdxhn]\s*(?:/\s*[mwfdxhn]\s*){1,2}"
GENDER_MARKER_PATTERN = re.compile(
    r"\(\s*(?:" + LETTER_MARKER + r"|all\s+genders?|alle\s+geschlechter|gn\*?)\s*\)"
    r"|(?<![\w/])" + LETTER_MARKER + r"(?![\w/])",
    re.IGNORECASE,
)

# Separators that can be left dangling after removing a marker,
# e.g. "Security Lead - m/f/d" -> "Security Lead -".
DANGLING_SEPARATOR_AT_END = re.compile(r"\s*[-–|,]\s*$")
DOUBLE_SEPARATOR = re.compile(r"([-–|])\s*[-–|]")  # "Engineer - - France" -> "Engineer - France"

# Looks for something like "<p>" or "</strong>" in text.
HTML_TAG_PATTERN = re.compile(r"</?[a-zA-Z][^>]*>")


# --- Small cleaning helpers (one value in, one value out) -------------------

def clean_text(value) -> str | None:
    """Strip whitespace from a text value. Empty strings and non-text become None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None  # "" counts as False, so empty text becomes None


def _html_to_text(html: str) -> str:
    """Extract the text from HTML, putting each block (paragraph, list item...) on its own line."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(INLINE_TAGS):
        tag.unwrap()  # remove the tag itself but keep the text inside it
    soup.smooth()  # merge text pieces that are now next to each other
    # get_text() also turns HTML entities into characters, e.g. "&amp;" -> "&".
    return soup.get_text(separator="\n")


def clean_description(html) -> str | None:
    """Convert an HTML job description into clean plain text."""
    if not isinstance(html, str):
        return None

    text = _html_to_text(html)

    # Some descriptions in the API are escaped twice: the raw value is
    # "&lt;p&gt;Hello&lt;/p&gt;" instead of "<p>Hello</p>". After the first pass
    # these still contain tags, so we run the HTML-to-text step once more.
    if HTML_TAG_PATTERN.search(text):
        text = _html_to_text(text)

    # Clean each line: turn any run of whitespace (spaces, tabs, non-breaking
    # spaces from "&nbsp;") into one space, and trim both ends.
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    # Drop blank lines and join the rest with single newlines.
    text = "\n".join(line for line in lines if line)
    return text or None


def clean_title(title) -> str | None:
    """Strip whitespace and remove gender markers like "(m/w/d)" from a job title."""
    if not isinstance(title, str):
        return None
    title = GENDER_MARKER_PATTERN.sub("", title)
    # Removing the marker can leave double spaces or separators behind,
    # e.g. "Engineer  - Berlin" or "Engineer - - France" or "Engineer -".
    title = re.sub(r"\s+", " ", title).strip()
    title = DOUBLE_SEPARATOR.sub(r"\1", title)
    title = DANGLING_SEPARATOR_AT_END.sub("", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title or None


def clean_list(value) -> list:
    """Return the value if it's a list, otherwise an empty list (for tags / job_types)."""
    return value if isinstance(value, list) else []


def clean_bool(value) -> bool | None:
    """Return the value if it's True/False, otherwise None."""
    return value if isinstance(value, bool) else None


# --- The main transform -----------------------------------------------------

def transform(jobs: list[dict]) -> pd.DataFrame:
    """Clean a list of raw job dicts and return a DataFrame matching the jobs table."""
    # Build a table from the list of dicts. Passing columns= keeps only those
    # fields; a field missing from a job becomes an empty (NaN) cell.
    df = pd.DataFrame(jobs, columns=RAW_COLUMNS)
    rows_in = len(df)

    # 1. Text columns. .map() calls the function once for every value in the column.
    for column in PLAIN_TEXT_COLUMNS:
        df[column] = df[column].map(clean_text)
    df["title"] = df["title"].map(clean_title)
    df["description"] = df["description"].map(clean_description)

    # 2. Other columns.
    df["remote"] = df["remote"].map(clean_bool)
    df["tags"] = df["tags"].map(clean_list)
    df["job_types"] = df["job_types"].map(clean_list)

    # 3. created_at is Unix time (seconds since 1970-01-01 UTC). Convert it to a
    # timezone-aware UTC datetime. errors="coerce" turns bad values into NaT
    # ("Not a Time", pandas' empty value for dates) instead of crashing.
    df["posted_at"] = pd.to_datetime(df["created_at"], unit="s", utc=True, errors="coerce")
    df = df.drop(columns=["created_at"])

    # 4. Drop rows missing a required field (the table needs slug and title).
    missing_required = df["slug"].isna() | df["title"].isna()
    df = df[~missing_required]  # "~" means NOT: keep rows that are NOT missing
    dropped_missing = int(missing_required.sum())

    # 5. Drop duplicate slugs, keeping the first one (slug is the primary key).
    rows_before_dedup = len(df)
    df = df.drop_duplicates(subset="slug", keep="first")
    dropped_duplicates = rows_before_dedup - len(df)

    # 6. Put the columns in table order and renumber the rows 0, 1, 2, ...
    df = df[FINAL_COLUMNS].reset_index(drop=True)

    # 7. pandas stores missing text as NaN (a special float). The database
    # step will want Python's None instead, so convert the text columns
    # to plain Python objects and replace NaN with None.
    for column in PLAIN_TEXT_COLUMNS + ["title", "description", "remote"]:
        df[column] = df[column].astype(object)
        df[column] = df[column].where(df[column].notna(), None)

    logger.info(
        "Data quality: rows in=%d, dropped (missing slug/title)=%d, "
        "dropped (duplicate slug)=%d, rows out=%d",
        rows_in, dropped_missing, dropped_duplicates, len(df),
    )
    return df


# --- Reading raw files ------------------------------------------------------

def find_newest_raw_file(raw_dir: Path = RAW_DATA_DIR) -> Path:
    """Return the newest data/raw/jobs_*.json file.

    The file names contain a timestamp like 20260923T101347Z, so sorting the
    names alphabetically also sorts them by time; the last one is the newest.
    """
    files = sorted(raw_dir.glob("jobs_*.json"))
    if not files:
        raise FileNotFoundError(f"No jobs_*.json files found in {raw_dir}. Run src.extract first.")
    return files[-1]


def load_raw(path: Path) -> list[dict]:
    """Read a raw JSON file written by src.extract and return the list of jobs."""
    with open(path, encoding="utf-8") as f:
        jobs = json.load(f)
    if not isinstance(jobs, list):
        raise ValueError(f"Expected a JSON list of jobs in {path}")
    logger.info("Loaded %d raw jobs from %s", len(jobs), path)
    return jobs


# --- Command-line entry point -----------------------------------------------

def main() -> None:
    """Transform one raw file (given on the command line, or the newest one) and show the result."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        # sys.argv is the list of command-line words; argv[1] is the optional path.
        path = Path(sys.argv[1]) if len(sys.argv) > 1 else find_newest_raw_file()
        jobs = load_raw(path)
    except (OSError, ValueError) as error:
        # OSError covers "file not found"; ValueError covers invalid JSON.
        logger.error("Transform failed: %s", error)
        sys.exit(1)

    df = transform(jobs)

    # print (not logging) here: this is output for a human to read.
    print()
    df.info()
    # Print the first 3 rows one at a time, one field per line
    # (long values are shortened to 80 characters).
    with pd.option_context("display.max_colwidth", 80):
        for index, row in df.head(3).iterrows():
            print(f"\n--- Example row {index} ---")
            print(row.to_string())


if __name__ == "__main__":
    main()
