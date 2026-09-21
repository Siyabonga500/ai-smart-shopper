"""Step 21: rate limits, security headers, static files, error pages, environment names and the deployment files."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from app import create_app
from app.extensions import db, limiter
from app.models import User
from tests.conftest import config_for_testing, forget_login_between_requests
from tests.test_admin import sign_in

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def limited_app():
    """``limited_app(**overrides)`` builds a test app with Flask-Limiter switched on and tiny limits (own empty database)."""
    opened = []

    def build(**overrides):
        settings = {"RATELIMIT_ENABLED": True, "RATELIMIT_AUTH": "3 per minute", "RATELIMIT_API": "4 per minute"}
        settings.update(overrides)
        with patch.multiple(config_for_testing(), **settings):
            app = forget_login_between_requests(create_app("testing"))
        context = app.app_context()
        context.push()
        db.create_all()
        limiter.reset()
        opened.append(context)
        return app

    yield build
    for context in reversed(opened):
        db.session.remove()
        db.drop_all()
        context.pop()


def add_user(email):
    user = User(FirstName="Thabo", LastName="Mokoena", Email=email, MicrosoftId=f"ms-{email}")
    db.session.add(user)
    db.session.commit()
    return user


# ------------------------------------------------------------------------------------------------ rate limits
def test_limits_are_off_in_the_test_config(client):
    for _ in range(30):
        assert client.post("/login", data={"Email": "a@example.com", "Password": "x"}).status_code == 401


def test_sign_in_is_limited_per_address_and_says_when_to_retry(limited_app):
    client = limited_app().test_client()
    codes = [
        client.post("/login", data={"Email": "a@example.com", "Password": "Wrong1!pass"}).status_code for _ in range(5)
    ]
    assert codes == [401, 401, 401, 429, 429]
    blocked = client.post("/login", data={"Email": "a@example.com", "Password": "Wrong1!pass"})
    assert "Retry-After" in blocked.headers and int(blocked.headers["Retry-After"]) > 0
    page = blocked.get_data(as_text=True)
    assert "slow down" in page.lower() and "error-art" in page  # the friendly page, not a bare 429


def test_only_posts_to_the_auth_forms_count(limited_app):
    client = limited_app().test_client()
    assert all(client.get("/login").status_code == 200 for _ in range(10))
    assert all(client.get("/register").status_code == 200 for _ in range(10))


def test_login_and_registration_share_one_auth_limit(limited_app):
    client = limited_app().test_client()
    assert [client.post("/register", data={}).status_code for _ in range(2)] == [200, 200]
    assert client.post("/login", data={"Email": "a@example.com", "Password": "x"}).status_code == 401  # third attempt
    assert client.post("/register", data={}).status_code == 429  # switching form does not reset the count
    assert client.post("/login", data={"Email": "a@example.com", "Password": "x"}).status_code == 429


def test_api_is_limited_per_student_not_per_address(limited_app):
    app = limited_app()
    first, second = add_user("one@example.com"), add_user("two@example.com")
    a, b = sign_in(app.test_client(), first), sign_in(app.test_client(), second)
    assert [a.get("/api/notifications").status_code for _ in range(5)] == [200, 200, 200, 200, 429]
    assert b.get("/api/notifications").status_code == 200  # same address, its own bucket
    blocked = a.get("/api/notifications")
    assert blocked.get_json() == {"error": "Too many requests. Please wait a minute and try again."}
    assert "Retry-After" in blocked.headers


def test_every_api_route_counts_against_one_shared_limit(limited_app):
    app = limited_app()
    a = sign_in(app.test_client(), add_user("shared@example.com"))
    routes = ["/api/notifications", "/api/list", "/api/savings", "/api/recommendations"]
    codes = [a.get(routes[i % len(routes)]).status_code for i in range(5)]
    assert 429 not in codes[:4] and codes[4] == 429  # four calls over four different routes used up "4 per minute"


def test_signed_out_api_calls_are_limited_by_address(limited_app):
    client = limited_app().test_client()
    assert [client.get("/api/geocode?q=x").status_code for _ in range(5)][-1] == 429


def test_health_is_never_limited(limited_app):
    client = limited_app(RATELIMIT_API="1 per minute", RATELIMIT_AUTH="1 per minute").test_client()
    assert all(client.get("/health").status_code == 200 for _ in range(10))


def test_the_default_limits(app):
    assert app.config["RATELIMIT_AUTH"] == "20 per minute" and app.config["RATELIMIT_API"] == "240 per minute"
    assert app.config["RATELIMIT_STORAGE_URI"].startswith(("memory://", "redis"))


# ------------------------------------------------------------------------------------------------ security headers
def unsafe_scripts(html):
    """Executable inline scripts without a nonce (JSON data blocks never run, so the CSP does not apply to them)."""
    tags = re.findall(r"<script\b[^>]*>", html)
    return [tag for tag in tags if " src=" not in tag and "nonce=" not in tag and "application/json" not in tag]


def test_security_headers_on_every_page(client):
    response = client.get("/login")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "geolocation=(self)" in response.headers["Permissions-Policy"]
    assert "Strict-Transport-Security" not in response.headers  # plain-http development


def test_csp_lists_the_hosts_the_pages_use_and_forbids_the_rest(client):
    csp = client.get("/login").headers["Content-Security-Policy"]
    directives = {part.split()[0]: part.split()[1:] for part in csp.split(";") if part.strip()}
    assert directives["default-src"] == ["'self'"]
    assert {"https://cdn.jsdelivr.net", "https://unpkg.com"} <= set(directives["script-src"])
    assert "'unsafe-inline'" not in directives["script-src"] and "'unsafe-eval'" not in directives["script-src"]
    assert any(item.startswith("'nonce-") for item in directives["script-src"])
    assert (
        "https://fonts.googleapis.com" in directives["style-src"]
        and "https://fonts.gstatic.com" in directives["font-src"]
    )
    assert directives["object-src"] == ["'none'"] and directives["frame-ancestors"] == ["'none'"]
    assert directives["connect-src"] == ["'self'"] and directives["base-uri"] == ["'self'"]


def test_the_nonce_changes_with_every_response_and_matches_the_inline_script(client):
    nonces = []
    for _ in range(2):
        response = client.get("/login")
        header = re.search(r"'nonce-([^']+)'", response.headers["Content-Security-Policy"]).group(1)
        inline = re.search(r'<script nonce="([^"]+)">window\.APP_CONFIG', response.get_data(as_text=True))
        assert inline and inline.group(1) == header
        nonces.append(header)
    assert nonces[0] != nonces[1]


@pytest.mark.parametrize("path", ["/login", "/register", "/terms"])
def test_no_inline_script_is_without_a_nonce_on_public_pages(client, path):
    html = client.get(path).get_data(as_text=True)
    assert unsafe_scripts(html) == []
    assert not re.search(r"\son(click|change|submit|load|error)=", html)


def test_no_inline_script_is_without_a_nonce_on_signed_in_pages(app, make_user):
    client = sign_in(app.test_client(), make_user(CellphoneNumber="0821234567"))
    for path in ("/dashboard", "/budget", "/search", "/list", "/profile", "/profile/edit", "/history"):
        html = client.get(path, follow_redirects=True).get_data(as_text=True)
        assert unsafe_scripts(html) == [], path
        assert not re.search(r"\son(click|change|submit|load|error)=", html), path


def test_production_forces_https_with_hsts():
    from app import config_by_name

    ProdConfig = config_by_name["production"]
    assert (
        ProdConfig.FORCE_HTTPS is True
        and ProdConfig.SESSION_COOKIE_SECURE is True
        and ProdConfig.REMEMBER_COOKIE_SECURE is True
    )
    assert ProdConfig.USE_WHITENOISE is True and ProdConfig.TRUST_PROXY is True
    with patch.multiple(config_for_testing(), FORCE_HTTPS=True):
        app = create_app("testing")
    app.testing = False
    secure = app.test_client().get("/login", headers={"X-Forwarded-Proto": "https"})
    assert secure.status_code == 200
    assert secure.headers["Strict-Transport-Security"].startswith("max-age=31536000")


# ------------------------------------------------------------------------------------------------ static files
def test_static_urls_carry_a_version_and_whitenoise_serves_them():
    with patch.multiple(config_for_testing(), USE_WHITENOISE=True):
        app = create_app("testing")
    with app.test_request_context():
        from flask import url_for

        url = url_for("static", filename="css/app.css")
    assert re.fullmatch(r"/static/css/app\.css\?v=\d+", url)
    response = app.test_client().get(url)
    assert response.status_code == 200 and "max-age=86400" in response.headers["Cache-Control"]
    assert b"--brand-deep" in response.get_data()
    assert app.test_client().get("/static/css/missing.css").status_code == 404


def test_files_uploaded_after_start_up_still_load(app):
    with patch.multiple(config_for_testing(), USE_WHITENOISE=True):
        served = create_app("testing")
    folder = Path(served.config["UPLOAD_FOLDER"])
    path = folder / "late-upload-test.png"
    path.write_bytes(b"\x89PNG-late")
    try:
        assert served.test_client().get("/static/uploads/late-upload-test.png").status_code == 200
    finally:
        path.unlink()


# ------------------------------------------------------------------------------------------------ error pages
def raising_app():
    app = create_app("testing")
    app.testing = False
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internals: db password is hunter2")

    return app


def test_500_page_is_friendly_and_leaks_nothing():
    response = raising_app().test_client().get("/boom")
    html = response.get_data(as_text=True)
    assert response.status_code == 500 and "error-art" in html
    assert "Something went wrong on our side" in html and "hunter2" not in html and "Traceback" not in html
    assert "spilled" not in html and "tipped-over" in html  # the 500 art (title text)


def test_404_and_403_pages_have_their_own_art_and_copy(client, app, make_user):
    page = client.get("/no/such/page")
    html = page.get_data(as_text=True)
    assert page.status_code == 404 and "Eish" in html and "magnifying glass" in html and 'class="error-art"' in html
    forbidden = sign_in(app.test_client(), make_user()).get("/admin/")
    assert forbidden.status_code == 403
    assert "Hayibo" in forbidden.get_data(as_text=True) and "padlock" in forbidden.get_data(as_text=True)


def test_error_pages_offer_a_way_back(client, app, make_user):
    assert "Sign in" in client.get("/nope").get_data(as_text=True)
    signed = sign_in(app.test_client(), make_user()).get("/nope").get_data(as_text=True)
    assert "Back to home" in signed and "Search products" in signed


def test_api_errors_are_json(client, app, make_user):
    missing = client.get("/api/does-not-exist")
    assert missing.status_code == 404 and missing.get_json() == {"error": "That was not found."}
    boom = raising_app()

    @boom.get("/api/boom")
    def api_boom():
        raise RuntimeError("x")

    response = boom.test_client().get("/api/boom")
    assert response.status_code == 500 and response.get_json() == {"error": "Something went wrong on our side."}


def test_413_page(app):
    app.config["MAX_CONTENT_LENGTH"] = 10
    response = app.test_client().post("/login", data={"Email": "a" * 100})
    assert response.status_code == 413 and "too big" in response.get_data(as_text=True).lower()


def test_every_error_scene_renders_valid_svg(app):
    from xml.etree import ElementTree

    from flask import render_template_string

    with app.test_request_context():
        for code in (400, 403, 404, 413, 429, 500):
            svg = render_template_string(
                '{% from "errors/_art.html" import scene %}{{ scene(code) }}', code=code
            ).strip()
            root = ElementTree.fromstring(svg)
            assert root.tag.endswith("svg") and root.attrib["role"] == "img"


# ------------------------------------------------------------------------------------------------ environment names
def _config_in_a_fresh_interpreter(env, expression):
    """Read a config value with the given environment. A subprocess keeps the shared config module untouched."""
    import json
    import os
    import subprocess
    import sys

    clean = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("MS_", "MICROSOFT_", "REDIS", "CACHE_", "OPEN_METEO", "RATELIMIT_"))
    }
    clean.update(env, PYTHONPATH=str(ROOT))
    code = f"import json; from app.config import BaseConfig as C; print(json.dumps({expression}))"
    done = subprocess.run([sys.executable, "-c", code], env=clean, capture_output=True, text=True, cwd=ROOT, check=True)
    return json.loads(done.stdout)


def test_deployment_env_names_are_accepted_as_aliases():
    env = {
        "MS_CLIENT_ID": "client-id",
        "MS_CLIENT_SECRET": "client-secret",
        "REDIS_URL": "redis://cache:6379/1",
        "OPEN_METEO_URL": "https://weather.example/v1",
    }
    values = _config_in_a_fresh_interpreter(
        env,
        "[C.MICROSOFT_CLIENT_ID, C.MICROSOFT_CLIENT_SECRET, C.CACHE_REDIS_URL, C.CACHE_TYPE, C.RATELIMIT_STORAGE_URI, C.OPEN_METEO_BASE_URL]",
    )
    assert values == [
        "client-id",
        "client-secret",
        "redis://cache:6379/1",
        "RedisCache",
        "redis://cache:6379/1",
        "https://weather.example/v1",
    ]


def test_the_long_names_win_over_the_aliases():
    env = {
        "MS_CLIENT_ID": "short",
        "MICROSOFT_CLIENT_ID": "long",
        "REDIS_URL": "redis://a/0",
        "CACHE_REDIS_URL": "redis://b/0",
    }
    assert _config_in_a_fresh_interpreter(env, "[C.MICROSOFT_CLIENT_ID, C.CACHE_REDIS_URL]") == ["long", "redis://b/0"]


def test_without_any_of_them_the_defaults_hold():
    assert _config_in_a_fresh_interpreter({}, "[C.CACHE_TYPE, C.RATELIMIT_STORAGE_URI, C.OPEN_METEO_BASE_URL]") == [
        "SimpleCache",
        "memory://",
        "https://api.open-meteo.com/v1",
    ]


def test_blank_alias_values_are_ignored(monkeypatch):
    from app.config import _env_first

    monkeypatch.setenv("A_NAME", "  ")
    monkeypatch.setenv("B_NAME", "value")
    assert _env_first("A_NAME", "B_NAME", "C_NAME") == "value" and _env_first("C_NAME") is None


# ------------------------------------------------------------------------------------------------ files
def test_gunicorn_waits_longer_than_an_apify_run(monkeypatch):
    import runpy

    monkeypatch.delenv("GUNICORN_TIMEOUT", raising=False)
    monkeypatch.setenv("APIFY_RUN_TIMEOUT", "120")
    assert runpy.run_path(str(ROOT / "gunicorn.conf.py"))["timeout"] == 150
    monkeypatch.setenv("GUNICORN_TIMEOUT", "45")
    assert runpy.run_path(str(ROOT / "gunicorn.conf.py"))["timeout"] == 45
    assert "--timeout" not in (ROOT / "docker-entrypoint.sh").read_text()  # one place decides


def test_csrf_tokens_last_as_long_as_the_session(app):
    assert app.config["WTF_CSRF_TIME_LIMIT"] is None


def test_procfile_runs_gunicorn():
    assert (ROOT / "Procfile").read_text().strip() == "web: gunicorn run:app"


def test_env_example_lists_the_deployment_variables():
    text = (ROOT / ".env.example").read_text()
    for name in (
        "SECRET_KEY",
        "DATABASE_URL",
        "APIFY_API_TOKEN",
        "PARSEBOT_API_KEY",
        "MS_CLIENT_ID",
        "MS_CLIENT_SECRET",
        "REDIS_URL",
        "OPEN_METEO_URL",
    ):
        assert re.search(rf"^#?\s*{name}=", text, re.M), name


def test_requirements_pin_the_production_packages():
    text = (ROOT / "requirements.txt").read_text().lower()
    for package in ("gunicorn", "whitenoise", "flask-limiter", "flask-talisman", "redis"):
        assert re.search(rf"^{package}==", text, re.M), package


def test_ci_workflow_runs_lint_tests_and_the_docker_build():
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for step in ("ruff check", "black --check", "pytest", "docker build"):
        assert step in text, step
