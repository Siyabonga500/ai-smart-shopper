"""``GET /health`` and ``flask healthcheck`` (Step 21)."""

import json
from datetime import timedelta
from unittest.mock import patch

from app.extensions import cache, db
from app.models import IntegrationSetting
from app.utils.dates import utcnow


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["app"] == "AI Smart Shopper"
    assert body["environment"] == "testing"
    assert set(body) == {"status", "app", "environment", "database", "cache", "last_api_sync", "timestamp"}


def test_health_needs_no_login_and_is_never_cached(client):
    response = client.get("/health")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"


def test_health_reports_503_when_database_is_down(client):
    with patch.object(db.session, "execute", side_effect=RuntimeError("db down: password=hunter2")):
        response = client.get("/health")
    assert response.status_code == 503
    body = response.get_json()
    assert body["database"] == "unavailable" and body["status"] == "unhealthy" and body["last_api_sync"] is None
    assert "hunter2" not in response.get_data(as_text=True)  # never echo the error text on a public page


def test_test_config_has_no_cache(client):
    assert client.get("/health").get_json()["cache"] == "disabled"


def test_a_working_cache_is_reported_ok(app, client, real_cache):
    app.config["CACHE_TYPE"] = "SimpleCache"
    assert client.get("/health").get_json()["cache"] == "ok"


def test_a_broken_cache_degrades_but_does_not_take_the_site_down(app, client, real_cache):
    app.config["CACHE_TYPE"] = "RedisCache"
    with patch.object(cache, "set", side_effect=ConnectionError("redis down")):
        response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["cache"] == "unavailable" and response.get_json()["status"] == "degraded"


def test_a_cache_that_forgets_is_reported_unavailable(app, client, real_cache):
    app.config["CACHE_TYPE"] = "SimpleCache"
    with patch.object(cache, "get", return_value=None):
        assert client.get("/health").get_json()["cache"] == "unavailable"


def test_last_api_sync_is_the_active_sources_last_sync(client):
    assert client.get("/health").get_json()["last_api_sync"] is None
    when = utcnow() - timedelta(hours=3)
    db.session.add(IntegrationSetting(Source="mock", IsActive=True, LastSyncOn=when))
    db.session.commit()
    stamp = client.get("/health").get_json()["last_api_sync"]
    assert stamp.startswith(when.strftime("%Y-%m-%dT%H:%M")) and stamp.endswith("+00:00")


def _https_forced_app():
    """A test app that behaves like production for Talisman (which never redirects while ``testing`` is on)."""
    from app import create_app
    from tests.conftest import config_for_testing

    with patch.object(config_for_testing(), "FORCE_HTTPS", True):
        forced = create_app("testing")
    forced.testing = False
    return forced


def test_health_answers_over_plain_http_even_when_https_is_forced(app):
    """Docker and load balancers probe over http; only /health is exempt from the redirect."""
    client = _https_forced_app().test_client()
    assert client.get("/health").status_code == 200
    redirect = client.get("/login")
    assert redirect.status_code == 301 and redirect.headers["Location"].startswith("https://")
    assert (
        client.get("/login", headers={"X-Forwarded-Proto": "https"}).status_code == 200
    )  # behind the platform's proxy


def test_healthcheck_command_prints_json_and_exits_zero(app):
    result = app.test_cli_runner().invoke(args=["healthcheck"])
    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "ok"


def test_healthcheck_command_exits_one_when_the_database_is_down(app):
    with patch.object(db.session, "execute", side_effect=RuntimeError("db down")):
        result = app.test_cli_runner().invoke(args=["healthcheck"])
    assert result.exit_code == 1
    assert json.loads(result.output)["database"] == "unavailable"
