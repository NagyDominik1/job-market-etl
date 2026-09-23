"""EXTRACT step: download job postings from the Arbeitnow Job Board API.

Fetches pages until there is no next page (or MAX_PAGES is reached) and saves
all jobs, unchanged, to data/raw/jobs_<UTC timestamp>.json.

Run it on its own with:
    .venv\\Scripts\\python -m src.extract

Settings (environment variables, both optional):
    API_URL    first page to fetch (default: the Arbeitnow API)
    MAX_PAGES  maximum number of pages to fetch (default: 3)
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Settings ---------------------------------------------------------------

DEFAULT_API_URL = "https://www.arbeitnow.com/api/job-board-api"
DEFAULT_MAX_PAGES = 3

# (connect timeout, read timeout) in seconds.
# connect = how long to wait to open the connection,
# read    = how long to wait for the server to send the response.
REQUEST_TIMEOUT = (5, 30)

# Pause between pages so we don't hammer a free API.
SECONDS_BETWEEN_PAGES = 1

USER_AGENT = "job-market-etl/0.1 (learning project; https://github.com/NagyDominik1/job-market-etl)"

# Raw files go to <project root>/data/raw, no matter which folder we run from.
# __file__ is src/extract.py, so two .parent calls get us to the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"

logger = logging.getLogger(__name__)


# --- HTTP session with retries ----------------------------------------------

def build_session() -> requests.Session:
    """Create a requests Session that automatically retries temporary failures."""
    retry = Retry(
        total=3,  # at most 3 retries in total (so up to 4 attempts)
        connect=3,  # retry when we can't connect (DNS failure, refused, connect timeout)
        read=3,  # retry when the server is too slow to respond (read timeout)
        status=3,  # retry on the HTTP status codes listed below
        other=0,  # don't retry any other kind of error
        # 429 = "Too Many Requests", 5xx = server-side errors. These are usually
        # temporary. Other 4xx (e.g. 404 Not Found) mean our request is wrong,
        # so retrying would not help: they are NOT in this list.
        status_forcelist=[429] + list(range(500, 600)),
        allowed_methods=["GET"],
        # Exponential backoff: wait roughly 0s, 2s, 4s before the retries.
        backoff_factor=1,
        # If the server sends a "Retry-After" header (common with 429), obey it.
        respect_retry_after_header=True,
    )
    session = requests.Session()
    # Use the retry rules for every http:// and https:// request of this session.
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


# --- Reading settings from the environment ----------------------------------

def get_max_pages() -> int:
    """Read MAX_PAGES from the environment, falling back to the default."""
    value = os.environ.get("MAX_PAGES", str(DEFAULT_MAX_PAGES))
    try:
        max_pages = int(value)
    except ValueError:
        raise ValueError(f"MAX_PAGES must be a whole number, got {value!r}")
    if max_pages < 1:
        raise ValueError(f"MAX_PAGES must be at least 1, got {max_pages}")
    return max_pages


# --- Downloading -------------------------------------------------------------

def fetch_page(session: requests.Session, url: str) -> dict:
    """Download one page and return the parsed JSON body.

    Raises an exception if the request fails (after retries) or the response
    doesn't look like an Arbeitnow page.
    """
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    # Turn non-retryable HTTP errors (e.g. 404) into an exception.
    response.raise_for_status()

    try:
        body = response.json()
    except requests.JSONDecodeError:
        content_type = response.headers.get("Content-Type")
        raise ValueError(f"Response from {response.url} is not JSON (Content-Type: {content_type})")

    # Basic sanity check: every page must have a "data" list of jobs.
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError(f"Unexpected response format from {url}")
    return body


def fetch_all_jobs(session: requests.Session, start_url: str, max_pages: int) -> list[dict]:
    """Follow the pagination links and return all jobs from all pages as one list."""
    all_jobs: list[dict] = []
    url = start_url
    page_number = 0

    while url is not None and page_number < max_pages:
        page_number += 1
        logger.info("Fetching page %d: %s", page_number, url)
        body = fetch_page(session, url)

        jobs = body["data"]
        all_jobs.extend(jobs)
        logger.info("Page %d: %d jobs", page_number, len(jobs))

        # The API tells us the URL of the next page in links.next.
        # On the last page it is null (None in Python), which ends the loop.
        url = body.get("links", {}).get("next")

        # Be polite: pause before requesting the next page.
        if url is not None and page_number < max_pages:
            time.sleep(SECONDS_BETWEEN_PAGES)

    if url is None:
        logger.info("Reached the last page of the API")
    else:
        logger.info("Stopped after MAX_PAGES=%d pages", max_pages)
    logger.info("Downloaded %d jobs from %d pages", len(all_jobs), page_number)
    return all_jobs


# --- Saving ------------------------------------------------------------------

def save_raw_jobs(jobs: list[dict]) -> Path:
    """Save the jobs, unchanged, to data/raw/jobs_<UTC timestamp>.json."""
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    file_path = RAW_DATA_DIR / f"jobs_{timestamp}.json"

    # ensure_ascii=False keeps characters like "ü" readable in the file.
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)

    logger.info("Saved %d jobs to %s", len(jobs), file_path)
    return file_path


# --- Main entry point --------------------------------------------------------

def extract() -> Path:
    """Run the whole extract step and return the path of the saved file.

    All-or-nothing: jobs are only written to disk after EVERY page downloaded
    successfully. If any page fails, an exception is raised and no file is written.
    """
    api_url = os.environ.get("API_URL", DEFAULT_API_URL)
    max_pages = get_max_pages()
    logger.info("Starting extract from %s (MAX_PAGES=%d)", api_url, max_pages)

    # "with" closes the session's network connections when we're done.
    with build_session() as session:
        jobs = fetch_all_jobs(session, api_url, max_pages)
    return save_raw_jobs(jobs)


def main() -> None:
    """Command-line entry point: set up logging, run extract, set the exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        extract()
    except (requests.RequestException, ValueError) as error:
        # RequestException covers connection errors, timeouts, HTTP errors and
        # "too many retries". ValueError covers bad settings and responses
        # that aren't JSON or don't look like an Arbeitnow page.
        logger.error("Extract failed, no file was written: %s", error)
        sys.exit(1)  # non-zero exit code = failure (useful for scripts / schedulers)


# This block only runs when the file is executed directly
# (python -m src.extract), not when another module imports it.
if __name__ == "__main__":
    main()
