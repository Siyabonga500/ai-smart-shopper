import pytest
from flask import render_template_string

from app import create_app
from app.config import TestConfig

PROTECTED = [
    "/dashboard",
    "/budget",
    "/search",
    "/list",
    "/list/summary",
    "/history",
    "/profile",
    "/profile/edit",
    "/admin/",
]
USER_PAGES = ["/", "/dashboard", "/budget", "/search", "/list", "/profile", "/profile/edit"]


@pytest.mark.parametrize("path", PROTECTED)
def test_protected_pages_redirect_to_login(client, path):
    response = client.get(path)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_page_is_public(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"Sign in with Microsoft" not in response.data  # removed from the sign-in page


@pytest.mark.parametrize("path", USER_PAGES)
def test_signed_in_user_can_open_every_main_view(client, make_user, login, path):
    login(make_user())
    assert client.get(path).status_code == 200


def test_dashboard_greets_the_student_by_first_name(client, make_user, login):
    login(make_user(first="Thabo"))
    response = client.get("/")
    assert b"Welcome, Thabo" in response.data


def test_navigation_has_the_five_pdf_items(client, make_user, login):
    login(make_user())
    html = client.get("/").get_data(as_text=True)
    for label in ("Home", "Budget", "Search", "List", "Profile"):
        assert f">{label}<" in html or f"</i>{label}" in html
    assert "bottom-nav-search" in html  # larger Search icon on mobile
    assert 'href="/history"' in html and "History" in html


def test_signed_in_user_is_sent_away_from_login(client, make_user, login):
    login(make_user())
    response = client.get("/login")
    assert response.status_code == 302


def test_logout(client, make_user, login):
    login(make_user())
    response = client.post("/logout")
    assert response.status_code == 302
    assert client.get("/budget/").status_code == 302  # session is gone


def test_logout_also_works_with_get(client, make_user, login):
    login(make_user())
    assert client.get("/logout").status_code == 302
    assert client.get("/budget/").status_code == 302


def test_admin_is_forbidden_for_regular_users(client, make_user, login):
    login(make_user(email="student@example.com"))
    assert client.get("/admin/").status_code == 403


def test_admin_allows_users_with_the_admin_flag(client, make_user, login):
    login(make_user(email="boss@example.com", IsAdmin=True))
    assert client.get("/admin/").status_code == 200


def test_an_email_address_alone_no_longer_grants_admin(client, make_user, login):
    login(make_user(email="admin@example.com"))  # the old ADMIN_EMAILS rule is gone: only the flag counts
    assert client.get("/admin/").status_code == 403


def test_404_uses_the_friendly_error_page(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert b"cannot find that page" in response.data


def test_zar_template_filter(app):
    with app.test_request_context():
        assert render_template_string("{{ 1750|zar }}") == "R1 750"
        assert render_template_string("{{ 29.99|zar }}") == "R29.99"


def test_client_config_exposes_durban_and_nsfas(client, make_user, login):
    login(make_user())
    html = client.get("/").get_data(as_text=True)
    assert "-29.8587" in html and "31.0218" in html
    assert "1750" in html


def test_csrf_is_enforced_when_enabled(monkeypatch):
    monkeypatch.setattr(TestConfig, "WTF_CSRF_ENABLED", True)
    app = create_app("testing")
    # CSRF runs before the login check, so a token-less POST is rejected outright.
    assert app.test_client().post("/logout").status_code == 400


def test_microsoft_blueprint_only_registered_when_configured(monkeypatch):
    monkeypatch.setenv("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # restored after the test
    assert "azure" not in create_app("testing").blueprints

    monkeypatch.setattr(TestConfig, "MICROSOFT_CLIENT_ID", "client-id")
    monkeypatch.setattr(TestConfig, "MICROSOFT_CLIENT_SECRET", "client-secret")
    app = create_app("testing")
    assert "azure" in app.blueprints
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/auth/azure" in rules
    assert "/auth/azure/authorized" in rules

    login_page = app.test_client().get("/login")
    assert b'href="/auth/microsoft"' not in login_page.data  # the button is gone even when configured


def test_theme_reaches_the_page_and_the_client_config(client, make_user, login):
    login(make_user())
    html = client.get("/").get_data(as_text=True)
    assert '<meta name="theme-color" content="#2C666E">' in html
    assert '"surface": "#F0EDEE"' in html or '"surface":"#F0EDEE"' in html
