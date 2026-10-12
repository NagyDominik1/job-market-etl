"""Shared HTTP settings for every extractor that calls an API.

Moved here from the v1 src/extract.py, so the Arbeitnow extractor and the
later ATS extractors (Greenhouse, Lever...) all use the same timeout and
retry rules.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# (connect timeout, read timeout) in seconds.
# connect = how long to wait to open the connection,
# read    = how long to wait for the server to send the response.
REQUEST_TIMEOUT = (5, 30)

USER_AGENT = "job-market-etl/0.2 (learning project; https://github.com/NagyDominik1/job-market-etl)"


def build_session(backoff_factor: float = 1) -> requests.Session:
    """Create a requests Session that automatically retries temporary failures.

    backoff_factor controls the wait between retries (roughly 0s, 2s, 4s with
    the default 1). Tests pass 0 so they don't have to wait.
    """
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
        # Exponential backoff: the wait doubles before each further retry.
        backoff_factor=backoff_factor,
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
