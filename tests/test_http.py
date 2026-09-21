import json

import pytest
import requests
import responses

from app.services import http
from app.services.http import ExternalAPIError, redact_url, request_with_retry

URL = "https://api.example.test/things"


@pytest.fixture
def no_sleep():
    delays = []
    return delays, delays.append


@responses.activate
def test_success_is_returned_untouched(no_sleep):
    responses.get(URL, json={"ok": True})
    delays, sleep = no_sleep
    assert request_with_retry("svc", "GET", URL, sleep=sleep).json() == {"ok": True}
    assert delays == []


@responses.activate
def test_429_and_5xx_are_retried_then_succeed(no_sleep):
    responses.get(URL, status=429, headers={"Retry-After": "2"})
    responses.get(URL, status=503)
    responses.get(URL, json={"ok": True})
    delays, sleep = no_sleep
    assert request_with_retry("svc", "GET", URL, sleep=sleep, retries=3).status_code == 200
    assert len(responses.calls) == 3
    assert delays[0] == 2.0  # Retry-After honoured
    assert 0.8 < delays[1] < 1.2  # attempt 2 backs off 0.5 * 2 = 1s, with jitter


@responses.activate
def test_gives_up_after_the_retry_budget(no_sleep):
    responses.get(URL, status=500)
    _, sleep = no_sleep
    with pytest.raises(ExternalAPIError) as info:
        request_with_retry("svc", "GET", URL, sleep=sleep, retries=2)
    assert len(responses.calls) == 3 and info.value.status == 500


@responses.activate
def test_client_errors_are_not_retried(no_sleep):
    responses.get(URL, status=404)
    _, sleep = no_sleep
    assert request_with_retry("svc", "GET", URL, sleep=sleep).status_code == 404
    assert len(responses.calls) == 1


@responses.activate
def test_network_errors_are_retried(no_sleep):
    responses.get(URL, body=requests.ConnectionError("boom"))
    responses.get(URL, json={"ok": 1})
    _, sleep = no_sleep
    assert request_with_retry("svc", "GET", URL, sleep=sleep).json() == {"ok": 1}
    assert len(responses.calls) == 2


def test_urls_are_stripped_of_credentials():
    assert "SECRET" not in redact_url("https://user:SECRET@host.test/p?api_key=SECRET&token=SECRET&q=milk")
    cleaned = redact_url("https://host.test/p?api_key=SECRET&q=milk")
    assert "q=milk" in cleaned and "SECRET" not in cleaned


@responses.activate
def test_every_call_is_logged_as_json_lines(app, no_sleep):
    http.init_api_logging(app)
    log_file = app.config["API_LOG_FILE"]
    open(log_file, "w").close()
    responses.get(URL, status=500)
    responses.get(URL, json={})
    _, sleep = no_sleep
    request_with_retry("svc", "GET", URL, sleep=sleep, params={"api_key": "SECRET", "q": "milk"})
    for handler in http.logging.getLogger(http.API_LOGGER_NAME).handlers:
        handler.flush()
    lines = [json.loads(line) for line in open(log_file) if line.strip()]
    assert [(entry["service"], entry["status"], entry["attempt"]) for entry in lines] == [
        ("svc", 500, 1),
        ("svc", 200, 2),
    ]
    assert "SECRET" not in open(log_file).read()


def test_logging_is_installed_once(app):
    http.init_api_logging(app)
    http.init_api_logging(app)
    handlers = [
        h for h in http.logging.getLogger(http.API_LOGGER_NAME).handlers if getattr(h, "_smartshopper_path", None)
    ]
    assert len(handlers) == 1


def test_error_text_never_carries_a_query_string(app):
    """A connection error quotes the whole URL, which can hold a home address or a key (found in the review)."""
    message = (
        "HTTPSConnectionPool(host='nominatim.example', port=443): Max retries exceeded with url: "
        "/search?q=12+Sample+Rd&key=SECRET123 (Caused by ConnectTimeoutError('timed out'))"
    )
    cleaned = http.redact_text(message)
    assert "SECRET123" not in cleaned and "Sample" not in cleaned
    assert "/search?***" in cleaned and "Caused by ConnectTimeoutError" in cleaned
    assert http.redact_text(RuntimeError("no url here")) == "no url here"
