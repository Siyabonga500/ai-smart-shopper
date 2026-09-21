"""Blueprint registration.

URL map (bottom/top navigation from the PDF, Dashboard view "Navigation"):

    Home    -> dashboard  /dashboard  (and /)
    Budget  -> budget     /budget
    Search  -> search     /search
    List    -> shopping   /list  (/list/summary; /shopping redirects here)
    history -> history    /history  (/history/<id>)
    Profile -> profile    /profile
    auth    -> /register /login /logout /auth/microsoft   (Microsoft callback: /auth/azure/authorized)
    pages   -> /terms
    api     -> /api/...   (JSON: geocoding, stores, search, list, weather, route)
    admin   -> /admin
"""

from flask import Flask, current_app

from app.extensions import limiter, user_or_ip
from app.routes.admin import bp as admin_bp
from app.routes.api import bp as api_bp
from app.routes.api_notifications import bp as notifications_api_bp
from app.routes.api_shop import bp as shop_api_bp
from app.routes.auth import bp as auth_bp
from app.routes.auth import build_microsoft_blueprint
from app.routes.budget import bp as budget_bp
from app.routes.dashboard import bp as dashboard_bp
from app.routes.history import bp as history_bp
from app.routes.pages import bp as pages_bp
from app.routes.profile import bp as profile_bp
from app.routes.search import bp as search_bp
from app.routes.shopping import bp as shopping_bp
from app.routes.shopping import legacy_bp as shopping_legacy_bp


def _auth_limit() -> str:
    return current_app.config["RATELIMIT_AUTH"]


def _api_limit() -> str:
    return current_app.config["RATELIMIT_API"]


# Flask-Limiter: sign-in and registration forms (POST only, per IP) and every /api route (per student, else per IP).
# The blueprints are module-level objects shared by every app the factory builds, so the limits are attached once, here.
limiter.shared_limit(
    _auth_limit, scope="auth", methods=["POST"], error_message="Too many attempts. Please wait a minute."
)(auth_bp)
for _api in (api_bp, shop_api_bp, notifications_api_bp):
    limiter.shared_limit(_api_limit, scope="api", key_func=user_or_ip)(_api)  # one shared counter, not one per route


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(shop_api_bp)
    app.register_blueprint(notifications_api_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(shopping_bp)
    app.register_blueprint(shopping_legacy_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(admin_bp)

    microsoft_bp = build_microsoft_blueprint(app)
    if microsoft_bp is not None:
        # Login: /auth/azure   Callback (register in Azure): /auth/azure/authorized
        app.register_blueprint(microsoft_bp, url_prefix="/auth")
