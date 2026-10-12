"""Arbeitnow Job Board API extractor (the v1 extract step, moved here).

Fetches pages until there is no next page (or ARBEITNOW_MAX_PAGES is reached)
and returns every job unchanged, with the API's "slug" as external_id.

Settings (environment variables, both optional):
    ARBEITNOW_API_URL    first page to fetch (default: the Arbeitnow API)
    ARBEITNOW_MAX_PAGES  maximum number of pages to fetch (default: 3)
"""

import logging
import os
import time

import requests

from src.extractors.base import Extractor, RawPosting
from src.http_client import REQUEST_TIMEOUT, build_session

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://www.arbeitnow.com/api/job-board-api"
DEFAULT_MAX_PAGES = 3

# Pause between pages so we don't hammer a free API.
SECONDS_BETWEEN_PAGES = 1


def get_max_pages() -> int:
    """Read ARBEITNOW_MAX_PAGES from the environment, falling back to the default."""
    value = os.environ.get("ARBEITNOW_MAX_PAGES", str(DEFAULT_MAX_PAGES))
    try:
        max_pages = int(value)
    except ValueError:
        raise ValueError(f"ARBEITNOW_MAX_PAGES must be a whole number, got {value!r}")
    if max_pages < 1:
        raise ValueError(f"ARBEITNOW_MAX_PAGES must be at least 1, got {max_pages}")
    return max_pages


def fetch_page(session: requests.Session, url: str, timeout=REQUEST_TIMEOUT) -> dict:
    """Download one page and return the parsed JSON body.

    Raises an exception if the request fails (after retries) or the response
    doesn't look like an Arbeitnow page.
    """
    response = session.get(url, timeout=timeout)
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


def fetch_all_jobs(
    session: requests.Session,
    start_url: str,
    max_pages: int,
    timeout=REQUEST_TIMEOUT,
    seconds_between_pages: float = SECONDS_BETWEEN_PAGES,
) -> list[dict]:
    """Follow the pagination links and return all jobs from all pages as one list.

    All-or-nothing: if any page fails, the exception propagates and nothing is
    returned, so we never store a half-downloaded result.
    """
    all_jobs: list[dict] = []
    url = start_url
    page_number = 0

    while url is not None and page_number < max_pages:
        page_number += 1
        logger.info("Fetching page %d: %s", page_number, url)
        body = fetch_page(session, url, timeout)

        jobs = body["data"]
        all_jobs.extend(jobs)
        logger.info("Page %d: %d jobs", page_number, len(jobs))

        # The API tells us the URL of the next page in links.next.
        # On the last page it is null (None in Python), which ends the loop.
        url = (body.get("links") or {}).get("next")

        # Be polite: pause before requesting the next page.
        if url is not None and page_number < max_pages:
            time.sleep(seconds_between_pages)

    if url is None:
        logger.info("Reached the last page of the API")
    else:
        logger.info("Stopped after ARBEITNOW_MAX_PAGES=%d pages", max_pages)
    logger.info("Downloaded %d jobs from %d pages", len(all_jobs), page_number)
    return all_jobs


class ArbeitnowExtractor(Extractor):
    name = "arbeitnow"
    kind = "api"

    def __init__(
        self,
        api_url: str | None = None,
        max_pages: int | None = None,
        session: requests.Session | None = None,
        timeout=REQUEST_TIMEOUT,
        seconds_between_pages: float = SECONDS_BETWEEN_PAGES,
    ):
        # Everything can be passed in (tests use a fake local server and no
        # waiting); otherwise the settings come from the environment.
        self.api_url = api_url or os.environ.get("ARBEITNOW_API_URL", DEFAULT_API_URL)
        self.max_pages = max_pages if max_pages is not None else get_max_pages()
        self.session = session
        self.timeout = timeout
        self.seconds_between_pages = seconds_between_pages

    def fetch(self) -> list[RawPosting]:
        logger.info("Starting Arbeitnow extract from %s (max pages=%d)", self.api_url, self.max_pages)
        # "with" closes the session's network connections when we're done.
        with (self.session or build_session()) as session:
            jobs = fetch_all_jobs(
                session, self.api_url, self.max_pages, self.timeout, self.seconds_between_pages,
            )

        postings: list[RawPosting] = []
        for job in jobs:
            slug = job.get("slug") if isinstance(job, dict) else None
            # The slug is the API's unique ID for a posting. Without it we can't
            # tell whether we've seen the posting before, so skip it.
            if not isinstance(slug, str) or not slug.strip():
                logger.warning("Skipping an Arbeitnow job without a slug")
                continue
            postings.append({"external_id": slug.strip(), "payload": job})
        return postings
