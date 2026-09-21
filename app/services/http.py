"""One place for outbound HTTP: retry with back-off, and an audit log of every external call.

Every request made through :func:`request_with_retry` is written to ``app/logs/api_calls.log`` as one
JSON line (service, method, URL with secrets removed, status, duration, attempt). API keys and tokens are
never logged: query parameters that look like credentials are redacted and headers are not recorded.

Retries: HTTP 429 and 5xx, plus connection errors and timeouts, are retried with exponential back-off
and jitter (a ``Retry-After`` header is honoured, capped). Other 4xx are returned to the caller untouched.
"""

import json
import logging
import random
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

API_LOGGER_NAME = "smartshopper.api_calls"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_SECRET_PARAMS = ("token", "key", "secret", "password", "auth", "signature", "sig")
_MAX_RETRY_AFTER = 30.0

_session = requests.Session()


class ExternalAPIError(RuntimeError):
    """An external service failed (after retries) or answered with something unusable."""

    def __init__(self, message: str, service: str = "", status: int | None = None):
        super().__init__(message)
        self.service = service
        self.status = status


# --- logging ----------------------------------------------------------------------------------
def init_api_logging(app) -> logging.Logger:
    """Attach a rotating JSON-lines file handler for ``app.config['API_LOG_FILE']`` (idempotent)."""
    logger = logging.getLogger(API_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    path = Path(app.config["API_LOG_FILE"]).resolve()

    for handler in logger.handlers:  # already pointing at this file? nothing to do
        if getattr(handler, "_smartshopper_path", None) == path:
            return logger
    for handler in list(logger.handlers):  # different file (e.g. another test config)
        if getattr(handler, "_smartshopper_path", None) is not None:
            logger.removeHandler(handler)
            handler.close()

    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler._smartshopper_path = path
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def redact_url(url: str) -> str:
    """Remove credentials from a URL before it is logged."""
    parts = urlsplit(url)
    query = [
        (key, "***" if any(word in key.lower() for word in _SECRET_PARAMS) else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, urlencode(query), ""))


_QUERY_IN_TEXT = re.compile(r"\?[^\s'\")>]*")


def redact_text(text) -> str:
    """An error message with every query string blanked (``...?q=12+Sample+Rd&key=abc`` becomes ``...?***``).

    Connection errors quote the whole request URL, which can hold a home address or an API key.
    """
    return _QUERY_IN_TEXT.sub("?***", str(text))


def log_api_call(service: str, method: str, url: str, *, status=None, duration_ms=None, attempt=1, error=None, **extra):
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "service": service,
        "method": method.upper(),
        "url": redact_url(url),
        "status": status,
        "duration_ms": None if duration_ms is None else round(duration_ms),
        "attempt": attempt,
    }
    if error:
        record["error"] = redact_text(error)[:300]
    record.update({key: value for key, value in extra.items() if value is not None})
    logging.getLogger(API_LOGGER_NAME).info(json.dumps(record, ensure_ascii=False))


# --- requests ---------------------------------------------------------------------------------
def _retry_delay(response, attempt: int, backoff: float) -> float:
    if response is not None:
        header = response.headers.get("Retry-After", "")
        if header.isdigit():
            return min(float(header), _MAX_RETRY_AFTER)
    return min(backoff * (2 ** (attempt - 1)), _MAX_RETRY_AFTER) * random.uniform(0.85, 1.15)


def request_with_retry(
    service: str,
    method: str,
    url: str,
    *,
    retries: int = 3,
    backoff: float = 0.5,
    timeout: float = 10,
    sleep=time.sleep,
    **kwargs,
) -> requests.Response:
    """Send a request, retrying 429/5xx/network errors ``retries`` times. Raises :class:`ExternalAPIError`.

    A response with a non-retryable error status (400, 401, 404 ...) is returned so the caller decides.
    """
    attempt = 0
    last_error = None
    while True:
        attempt += 1
        started = time.monotonic()
        response = None
        try:
            response = _session.request(method, url, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
            log_api_call(
                service, method, url, duration_ms=(time.monotonic() - started) * 1000, attempt=attempt, error=exc
            )
        else:
            log_api_call(
                service,
                method,
                url,
                status=response.status_code,
                duration_ms=(time.monotonic() - started) * 1000,
                attempt=attempt,
            )
            if response.status_code not in RETRY_STATUSES:
                return response
            last_error = None

        if attempt > retries:
            status = response.status_code if response is not None else None
            reason = f"HTTP {status}" if status else type(last_error).__name__
            raise ExternalAPIError(
                f"{service} request failed after {attempt} attempts ({reason})", service=service, status=status
            )
        sleep(_retry_delay(response, attempt, backoff))
