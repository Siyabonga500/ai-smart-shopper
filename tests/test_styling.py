"""Step 18: the design tokens, the fonts, the navigation and the loading skeletons."""

import re
from pathlib import Path

import pytest

from app.extensions import db
from tests.test_admin import sign_in

CSS_PATH = Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css"
CSS = CSS_PATH.read_text()


def token(name):
    match = re.search(rf"--{re.escape(name)}:\s*([^;]+);", CSS)
    assert match, f"--{name} is not defined"
    return match.group(1).strip().split("/*")[0].strip()


@pytest.mark.parametrize(
    "name, value",
    [
        ("brand", "#2C666E"),
        ("surface", "#F0EDEE"),  # the project's standing brand colours
        ("brand-deep", "#0B3D3B"),
        ("accent", "#F26B3A"),
        ("success", "#2E9E6B"),
        ("warning-fill", "#E6A817"),
        ("danger", "#D9534F"),
    ],
)
def test_colour_tokens(name, value):
    assert token(name).upper() == value


def test_type_scale_is_12_14_16_20_28():
    sizes = {
        name: float(token(name).removesuffix("rem")) * 16 for name in ("fs-xs", "fs-sm", "fs-md", "fs-lg", "fs-xl")
    }
    assert list(sizes.values()) == [12, 14, 16, 20, 28]


def test_card_tokens():
    assert token("radius-card") == "12px" and token("pad-card") == "16px" and token("content-max") == "1200px"
    assert re.search(r"\.app-card\s*\{[^}]*border-radius:\s*var\(--radius-card\)", CSS)
    assert re.search(r"\.app-card\s*\{[^}]*box-shadow:\s*var\(--shadow-card\)", CSS)


def test_no_stray_font_sizes_outside_the_scale():
    """Every rem font-size after the token block uses a token (clamp() and em are fine)."""
    body = CSS.split("body { background")[1]
    assert re.findall(r"font-size:\s*[0-9.]+rem", body) == []


def test_the_cta_colour_keeps_readable_text():
    """White on #F26B3A is 3:1; the CTA wears dark ink instead (>= 4.5:1)."""

    def luminance(hex_colour):
        channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    for fill in (token("accent"), token("accent-hover")):
        light, dark = sorted([luminance(fill), luminance(token("accent-ink"))], reverse=True)
        assert (light + 0.05) / (dark + 0.05) >= 4.5, fill
    assert re.search(r"\.btn-accent\s*\{[^}]*--bs-btn-color:\s*var\(--accent-ink\)", CSS)


def test_text_status_colours_pass_on_white():
    def luminance(hex_colour):
        channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    for name in ("success-text", "warning-text", "danger-text"):
        assert 1.05 / (luminance(token(name)) + 0.05) >= 4.5, name


def test_pages_load_inter_and_the_stylesheet(client):
    html = client.get("/login").get_data(as_text=True)
    assert "family=Inter" in html and "css/app.css" in html and "main.css" not in html
    assert (CSS_PATH.parent / "main.css").exists() is False


def test_bottom_nav_has_five_items_and_a_search_bubble(app, make_user):
    user = make_user()
    html = sign_in(app.test_client(), user).get("/dashboard").get_data(as_text=True)
    nav = html.split('class="app-bottom-nav')[1].split("</nav>")[0]
    assert nav.count("bottom-nav-item") == 5 and "search-bubble" in nav
    assert [label for label in ("Home", "Budget", "Search", "List", "Profile") if f">{label}<" in nav] == [
        "Home",
        "Budget",
        "Search",
        "List",
        "Profile",
    ]
    assert re.search(r"\.search-bubble\s*\{[^}]*width:\s*56px", CSS)  # larger than the other icons


def test_desktop_uses_the_top_bar_and_the_phone_bar_is_hidden_on_desktop(app, make_user):
    html = sign_in(app.test_client(), make_user()).get("/dashboard").get_data(as_text=True)
    assert "app-topbar d-none d-lg-flex" in html and 'class="app-bottom-nav d-lg-none"' in html


def test_api_driven_sections_ship_a_skeleton(app, make_user):
    client = sign_in(app.test_client(), make_user())
    dashboard = client.get("/dashboard").get_data(as_text=True)
    assert (
        'id="wx-skeleton"' in dashboard and "Loading the weather" in dashboard and 'id="wx-content" hidden' in dashboard
    )
    search = client.get("/search").get_data(as_text=True)
    assert "sk sk-w80" in search and "Loading stores" in search
    js = (CSS_PATH.parents[1] / "js" / "search.js").read_text()
    assert "product-card compact skeleton" in js


def test_skeletons_respect_reduced_motion():
    animated = re.findall(r"@media \(prefers-reduced-motion: no-preference\)\s*\{[^}]*animation:\s*sk-shimmer", CSS)
    assert len(animated) >= 2
    assert not re.search(r"^\.sk\s*\{[^}]*animation", CSS, re.M)


def test_admin_pages_use_the_same_stylesheet(app, make_user):
    admin = make_user(email="a@example.com", IsAdmin=True, CellphoneNumber="0831112222")
    db.session.commit()
    html = sign_in(app.test_client(), admin).get("/admin/").get_data(as_text=True)
    assert "css/app.css" in html and "family=Inter" in html
