"""AI Smart Shopper - application factory."""

import logging
import os
from pathlib import Path

from flask import Flask, flash, jsonify, make_response, redirect, render_template, request, url_for
from flask_talisman import Talisman

from app.cli import register_cli
from app.config import DEFAULT_CONFIG_NAME, config_by_name
from app.data.dut_residences import grouped_residences
from app.extensions import (
    bcrypt,
    cache,
    csrf,
    db,
    geocode_limiter,
    limiter,
    login_limiter,
    login_manager,
    migrate,
    search_limiter,
)
from app.services import health
from app.services.http import init_api_logging
from app.services.notifications import bell, list_item_count
from app.utils.formatters import format_date, format_datetime, format_zar, signed_zar, zar_parts
from app.utils.geo import default_map_center


def create_app(config_name: str | None = None) -> Flask:
    """Build and configure a Flask app.

    ``config_name`` is ``development`` | ``production`` | ``testing``; when
    omitted it is read from ``APP_ENV`` (then ``FLASK_ENV``) and defaults to
    development.
    """
    config_name = config_name or os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or DEFAULT_CONFIG_NAME
    try:
        config_class = config_by_name[config_name]
    except KeyError:
        raise ValueError(f"Unknown config {config_name!r}; choose one of {sorted(config_by_name)}") from None

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    app.config["CONFIG_NAME"] = config_name
    config_class.init_app(app)

    _configure_logging(app)
    _init_extensions(app)
    _register_blueprints(app)
    _register_template_helpers(app)
    register_cli(app)
    _register_error_handlers(app)
    _register_health_check(app)
    _register_static_versioning(app)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    # Order matters: WhiteNoise is added first so the proxy fix (added last) runs first on each request.
    if app.config.get("USE_WHITENOISE"):
        from whitenoise import WhiteNoise

        # Files it does not know (profile pictures uploaded after start-up) fall through to Flask's own /static route.
        app.wsgi_app = WhiteNoise(
            app.wsgi_app,
            root=app.static_folder,
            prefix=app.static_url_path.strip("/"),
            max_age=app.config["WHITENOISE_MAX_AGE"],
        )

    if app.config.get("TRUST_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    return app


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)  # SQLite cannot ALTER columns without batch mode
    login_manager.init_app(app)
    bcrypt.init_app(app)

    @app.before_request
    def _allow_product_photos():
        # Registered before CSRFProtect so the bigger limit is in place before the form is first read.
        if request.path.startswith("/admin/products"):
            request.max_content_length = app.config["ADMIN_PRODUCT_MAX_CONTENT_LENGTH"]

    csrf.init_app(app)

    cache_config = {
        "CACHE_TYPE": app.config["CACHE_TYPE"],
        "CACHE_DEFAULT_TIMEOUT": app.config["CACHE_DEFAULT_TIMEOUT"],
    }
    if app.config.get("CACHE_REDIS_URL"):
        cache_config["CACHE_REDIS_URL"] = app.config["CACHE_REDIS_URL"]
    cache.init_app(app, config=cache_config)

    # The limiters are process-wide objects: apply this app's limits and start clean.
    login_limiter.limit = app.config["LOGIN_FAILURE_LIMIT"]
    login_limiter.window = app.config["LOGIN_FAILURE_WINDOW"]
    geocode_limiter.limit = app.config["GEOCODE_REQUEST_LIMIT"]
    geocode_limiter.window = app.config["GEOCODE_REQUEST_WINDOW"]
    search_limiter.limit = app.config["SEARCH_REQUEST_LIMIT"]
    search_limiter.window = app.config["SEARCH_REQUEST_WINDOW"]
    login_limiter.clear()
    geocode_limiter.clear()
    search_limiter.clear()

    init_api_logging(app)  # app/logs/api_calls.log: every external call, secrets redacted

    limiter.init_app(app)  # request-rate caps (RATELIMIT_* config); the limits are attached in routes/__init__.py
    talisman = Talisman()  # one per app: its options are stored on the instance
    app.extensions["shopper_talisman"] = talisman
    talisman.init_app(
        app,
        force_https=app.config["FORCE_HTTPS"],
        force_https_permanent=True,
        strict_transport_security=app.config["FORCE_HTTPS"],
        strict_transport_security_max_age=app.config["HSTS_SECONDS"],
        content_security_policy=app.config["CSP"],
        content_security_policy_nonce_in=["script-src"],  # <script nonce="{{ csp_nonce() }}"> for inline scripts
        frame_options="DENY",
        referrer_policy="strict-origin-when-cross-origin",
        permissions_policy={"geolocation": "(self)", "camera": "()", "microphone": "()", "payment": "()"},
        session_cookie_secure=app.config.get("SESSION_COOKIE_SECURE", False),
    )

    @login_manager.unauthorized_handler
    def _unauthorized():
        if request.path.startswith("/api/"):
            return jsonify(error="Please sign in to continue."), 401
        flash(login_manager.login_message, login_manager.login_message_category)
        return redirect(url_for(login_manager.login_view, next=request.full_path.rstrip("?")))

    # Importing the models registers them on db.metadata (needed by Flask-Migrate)
    # and registers the Flask-Login user_loader.
    from app import models  # noqa: F401


def _register_blueprints(app: Flask) -> None:
    from app.routes import register_blueprints

    register_blueprints(app)


def _register_template_helpers(app: Flask) -> None:
    app.add_template_filter(format_zar, "zar")
    app.add_template_filter(signed_zar, "signed_zar")
    app.add_template_filter(zar_parts, "zar_parts")
    app.add_template_filter(format_datetime, "sast_datetime")
    app.add_template_filter(format_date, "sast_date")
    app.jinja_env.globals["grouped_residences"] = grouped_residences
    app.jinja_env.globals["bell"] = bell  # the bell (notifications)
    app.jinja_env.globals["nav_list_count"] = list_item_count  # the red badge on the List tab

    @app.context_processor
    def inject_globals():
        cfg = app.config
        from app.services.home import tip_of_the_day
        from app.utils.dates import today_sast

        today = today_sast()
        return {
            "app_name": cfg["APP_NAME"],
            "footer_tip": tip_of_the_day(today),
            "current_year": today.year,
            "theme": cfg["THEME"],
            "nsfas_allowance": cfg["NSFAS_MONTHLY_ALLOWANCE"],
            # Read by static/js/main.js as window.APP_CONFIG
            "client_config": {
                "currencySymbol": cfg["CURRENCY_SYMBOL"],
                "currencyCode": cfg["CURRENCY_CODE"],
                "nsfasAllowance": float(cfg["NSFAS_MONTHLY_ALLOWANCE"]),
                "theme": dict(cfg["THEME"]),  # for Chart.js colours
                "map": {
                    "center": default_map_center(),  # Durban
                    "zoom": cfg["DEFAULT_MAP_ZOOM"],
                    "tileUrl": cfg["MAP_TILE_URL"],
                    "attribution": cfg["MAP_ATTRIBUTION"],
                },
            },
        }


ERRORS = {
    400: ("Eish, that did not work.", "Something in that request was not right. Go back and try again."),
    403: (
        "Hayibo! That page is not for you.",
        "You do not have access to this area. If you think that is a mistake, ask an admin.",
    ),
    404: (
        "Eish! We cannot find that page.",
        "The link may be old or have a typing mistake. Let us get you back to your budget.",
    ),
    413: ("That file is too big.", "Pictures can be up to 2MB. Choose a smaller one and try again."),
    429: ("Ag, slow down a little.", "You have made a lot of requests very quickly. Wait a minute and try again."),
    500: (
        "Eish! Something went wrong on our side.",
        "It is not you. Please try again in a minute. Your budget and list are safe.",
    ),
}
# Short lines the JSON API and older callers use.
API_MESSAGES = {
    403: "You do not have access to this.",
    404: "That was not found.",
    413: "That file is too large (max 2MB).",
    429: "Too many requests. Please wait a minute and try again.",
    500: "Something went wrong on our side.",
}


def _error_response(code: int):
    """A JSON body for /api/... requests, the friendly illustrated page for everything else."""
    if request.path.startswith("/api/"):
        response = jsonify(error=API_MESSAGES.get(code, ERRORS[code][0]))
    else:
        title, message = ERRORS[code]
        response = make_response(render_template("errors/error.html", code=code, title=title, message=message))
    response.status_code = code
    return response


def _register_error_handlers(app: Flask) -> None:
    for code in ERRORS:
        if code == 500:
            continue

        def handler(_error, code=code):
            return _error_response(code)  # Flask-Limiter adds Retry-After to a 429 itself

        app.register_error_handler(code, handler)

    @app.errorhandler(500)
    def server_error(_error):
        db.session.rollback()
        return _error_response(500)


def _register_health_check(app: Flask) -> None:
    @app.get("/health")
    @app.extensions["shopper_talisman"](force_https=False)  # probes (Docker, load balancers) call it over plain http
    def health_page():
        """Liveness plus what the app depends on. 200 when the database answers, 503 when it does not."""
        payload, healthy = health.report()
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store"
        return response, (200 if healthy else 503)


def _register_static_versioning(app: Flask) -> None:
    """``/static/css/app.css?v=<file time>``: a new deploy changes the URL, so browsers never keep an old copy."""
    import os

    @app.url_defaults
    def add_static_version(endpoint, values):
        if endpoint == "static" and "v" not in values and values.get("filename"):
            try:
                values["v"] = int(os.stat(os.path.join(app.static_folder, values["filename"])).st_mtime)
            except OSError:
                pass


def _configure_logging(app: Flask) -> None:
    if not app.debug and not app.testing:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
