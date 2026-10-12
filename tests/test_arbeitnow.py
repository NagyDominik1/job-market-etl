"""Tests for the Arbeitnow extractor against a fake HTTP server.

Instead of mocking requests.get (which would skip the retry logic), a tiny
real HTTP server runs on localhost in a background thread. So these tests
exercise the real timeout and retry behaviour of build_session(), with no
extra library and no internet.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from src.extractors.arbeitnow import ArbeitnowExtractor
from src.http_client import build_session


class FakeApi:
    """A local HTTP server that answers with a scripted list of responses.

    Each response is (status code, JSON body, delay in seconds). Requests after
    the last response get the last one again. `requests` records every path.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                index = min(len(api.requests), len(api.responses) - 1)
                api.requests.append(self.path)
                status, body, delay = api.responses[index]
                time.sleep(delay)
                data = json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client already gave up (timeout test)

            def log_message(self, *args):
                pass  # keep the test output clean

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)  # port 0 = any free port
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_api():
    servers = []

    def start(responses):
        server = FakeApi(responses)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def page(jobs, next_url=None):
    return {"data": jobs, "links": {"next": next_url}}


def extractor_for(api, **kwargs):
    # backoff_factor=0: retry immediately, so the tests don't wait.
    return ArbeitnowExtractor(
        api_url=api.url + "/api", max_pages=kwargs.pop("max_pages", 3),
        session=build_session(backoff_factor=0), seconds_between_pages=0, **kwargs,
    )


def test_jobs_are_returned_with_slug_as_external_id(fake_api):
    api = fake_api([(200, page([{"slug": "data-engineer-acme", "title": "Data Engineer"}]), 0)])
    postings = extractor_for(api).fetch()
    assert postings == [{
        "external_id": "data-engineer-acme",
        "payload": {"slug": "data-engineer-acme", "title": "Data Engineer"},
    }]


def test_follows_pagination_until_the_last_page(fake_api):
    api = fake_api([])
    api.responses = [
        (200, page([{"slug": "a"}], next_url=api.url + "/api?page=2"), 0),
        (200, page([{"slug": "b"}]), 0),
    ]
    assert [p["external_id"] for p in extractor_for(api).fetch()] == ["a", "b"]
    assert api.requests == ["/api", "/api?page=2"]


def test_stops_after_max_pages(fake_api):
    api = fake_api([])
    api.responses = [(200, page([{"slug": "a"}], next_url=api.url + "/api?page=2"), 0)]
    assert len(extractor_for(api, max_pages=1).fetch()) == 1
    assert len(api.requests) == 1


def test_jobs_without_slug_are_skipped(fake_api):
    api = fake_api([(200, page([{"slug": "a"}, {"title": "no slug"}, {"slug": "  "}]), 0)])
    assert [p["external_id"] for p in extractor_for(api).fetch()] == ["a"]


def test_server_errors_are_retried_then_succeed(fake_api):
    api = fake_api([(503, {}, 0), (500, {}, 0), (200, page([{"slug": "a"}]), 0)])
    assert len(extractor_for(api).fetch()) == 1
    assert len(api.requests) == 3  # 2 failures + 1 success


def test_gives_up_after_three_retries(fake_api):
    api = fake_api([(503, {}, 0)])
    with pytest.raises(requests.RequestException):
        extractor_for(api).fetch()
    assert len(api.requests) == 4  # 1 attempt + 3 retries


def test_client_errors_are_not_retried(fake_api):
    api = fake_api([(404, {}, 0)])
    with pytest.raises(requests.HTTPError):
        extractor_for(api).fetch()
    assert len(api.requests) == 1  # retrying a 404 would not help


def test_slow_server_times_out_and_is_retried(fake_api):
    # The server needs 1 s to answer, but we only wait 0.2 s for the response.
    api = fake_api([(200, page([{"slug": "a"}]), 1.0)])
    started = time.monotonic()
    with pytest.raises(requests.RequestException):
        extractor_for(api, timeout=(1, 0.2)).fetch()
    assert len(api.requests) == 4                 # every attempt timed out
    assert time.monotonic() - started < 3         # and we didn't wait for the slow server


def test_timeout_then_success(fake_api):
    api = fake_api([(200, {}, 1.0), (200, page([{"slug": "a"}]), 0)])
    assert len(extractor_for(api, timeout=(1, 0.2)).fetch()) == 1
    assert len(api.requests) == 2


def test_response_that_is_not_an_arbeitnow_page_is_rejected(fake_api):
    api = fake_api([(200, {"unexpected": True}, 0)])
    with pytest.raises(ValueError, match="Unexpected response format"):
        extractor_for(api).fetch()
