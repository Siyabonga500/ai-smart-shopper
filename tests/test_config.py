from decimal import Decimal

import pytest

from app import create_app
from app.config import ProdConfig, _normalise_postgres_url, config_by_name


def test_business_constants_from_the_pdf(app):
    cfg = app.config
    assert cfg["NSFAS_MONTHLY_ALLOWANCE"] == Decimal("1750.00")
    assert cfg["CURRENCY_CODE"] == "ZAR"
    assert cfg["CURRENCY_SYMBOL"] == "R"
    assert cfg["BUDGET_CATEGORIES"] == ("Grocery", "Toiletries", "Clothes", "Electronics")
    assert cfg["COMBINED_BUDGET_CATEGORY"] == "Combined"
    assert cfg["DASHBOARD_RECENT_ITEM_COUNT"] == 4
    assert cfg["SPENDING_HISTORY_MONTHS"] == 6
    assert cfg["MAX_UPLOAD_BYTES"] == 2 * 1024 * 1024


def test_durban_is_the_default_map_centre(app):
    center = app.config["DEFAULT_MAP_CENTER"]
    assert center == {"lat": -29.8587, "lng": 31.0218}


def test_config_names():
    assert set(config_by_name) == {"development", "production", "testing"}


def test_unknown_config_name_raises():
    with pytest.raises(ValueError, match="Unknown config"):
        create_app("staging")


def test_dev_uses_sqlite_and_test_uses_memory():
    assert config_by_name["development"].SQLALCHEMY_DATABASE_URI.startswith("sqlite")
    assert config_by_name["testing"].SQLALCHEMY_DATABASE_URI == "sqlite:///:memory:"


def test_postgres_scheme_is_normalised():
    assert _normalise_postgres_url("postgres://u:p@h/db") == "postgresql://u:p@h/db"
    assert _normalise_postgres_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"
    assert _normalise_postgres_url(None) is None


def test_production_requires_database_url(monkeypatch):
    monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", None)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app("production")


def test_production_rejects_default_secret_key(monkeypatch):
    monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql://u:p@localhost/db")
    monkeypatch.setattr(ProdConfig, "SECRET_KEY", "dev-only-change-me")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app("production")


@pytest.mark.parametrize("key", ["change-me", "CHANGE-ME", "changeme", "", "too-short-for-production"])
def test_production_rejects_placeholder_and_short_secret_keys(monkeypatch, key):
    monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql://u:p@localhost/db")
    monkeypatch.setattr(ProdConfig, "SECRET_KEY", key)
    with pytest.raises(RuntimeError, match="at least 32"):
        create_app("production")


def test_production_boots_with_valid_settings(monkeypatch):
    monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql://u:p@localhost/db")
    monkeypatch.setattr(ProdConfig, "SECRET_KEY", "f91d8ef84c95814550e68a6faaa368d2159f2d039490edb43877fd0cc1f02d4c")
    app = create_app("production")
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["DEBUG"] is False


def test_brand_theme_colours(app):
    assert app.config["THEME"] == {"primary": "#2C666E", "surface": "#F0EDEE"}


def test_css_uses_the_same_brand_colours_as_config(app):
    """Guards against the CSS and the config (used by Chart.js) drifting apart."""
    from pathlib import Path

    css = (Path(app.root_path) / "static" / "css" / "app.css").read_text().upper()
    assert app.config["THEME"]["primary"].upper() in css
    assert app.config["THEME"]["surface"].upper() in css


def test_blank_environment_values_fall_back_to_defaults(monkeypatch):
    """`DEV_DATABASE_URL=` left empty in .env must not crash the app."""
    import importlib

    import app.config as config_module

    monkeypatch.setenv("DEV_DATABASE_URL", "")
    monkeypatch.setenv("SECRET_KEY", "")
    monkeypatch.setenv("MICROSOFT_TENANT", "")
    try:
        reloaded = importlib.reload(config_module)
        assert reloaded.DevConfig.SQLALCHEMY_DATABASE_URI == "sqlite:///dev.db"
        assert reloaded.BaseConfig.SECRET_KEY == "dev-only-change-me"
        assert reloaded.BaseConfig.MICROSOFT_TENANT == "common"
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)
