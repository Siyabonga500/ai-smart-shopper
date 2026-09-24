"""Configuration classes for AI Smart Shopper (Dev / Prod / Test).

Business constants that come straight from the design PDF
("Budget Shopper Views Plan") are defined once in ``BaseConfig`` so every
blueprint, template and service reads the same value.
"""

import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

_INSECURE_DEFAULT_KEY = "dev-only-change-me"
# Values people copy from an example file. Production refuses these, and any key shorter than MIN_SECRET_KEY_LENGTH.
_PLACEHOLDER_KEYS = {_INSECURE_DEFAULT_KEY, "change-me", "changeme", "secret", "your-secret-key", "replace-me"}
MIN_SECRET_KEY_LENGTH = 32


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_first(*names: str) -> str | None:
    """The first of these environment variables that is set and not blank (lets deployments use their own names)."""
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def _env_list(name: str) -> list[str]:
    return [item.strip().lower() for item in os.getenv(name, "").split(",") if item.strip()]


def _normalise_postgres_url(url: str | None) -> str | None:
    """Heroku/Render style ``postgres://`` URLs are rejected by SQLAlchemy 2."""
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


class BaseConfig:
    APP_NAME = "AI Smart Shopper"

    # --- Core Flask -------------------------------------------------------
    SECRET_KEY = os.getenv("SECRET_KEY") or _INSECURE_DEFAULT_KEY  # blank counts as unset
    DEBUG = False
    TESTING = False
    TRUST_PROXY = _env_bool("TRUST_PROXY")

    # --- Database ---------------------------------------------------------
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Sessions / CSRF (Flask-Login + Flask-WTF) --------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    WTF_CSRF_ENABLED = True
    # A CSRF token lasts as long as the session. (The 1-hour default made a list left open in a shop stop working.)
    WTF_CSRF_TIME_LIMIT = None

    # --- Microsoft OAuth (flask-dance / Azure AD) --------------------------
    MICROSOFT_CLIENT_ID = _env_first("MICROSOFT_CLIENT_ID", "MS_CLIENT_ID")
    MICROSOFT_CLIENT_SECRET = _env_first("MICROSOFT_CLIENT_SECRET", "MS_CLIENT_SECRET")
    MICROSOFT_TENANT = os.getenv("MICROSOFT_TENANT") or "common"

    # --- Sign-up validation: also ask DNS whether the email's domain exists (off by default; tests never hit DNS)
    EMAIL_DNS_CHECK = _env_bool("EMAIL_DNS_CHECK")

    # --- Admin portal: access is the User.IsAdmin flag. Create the first admin with `flask make-admin <email>`.
    # The built-in admin account (`flask seed-admin`, also created on its first sign-in if it does not exist yet).
    # Set DEFAULT_ADMIN_PASSWORD in the environment of a public site: the default below is written in the README.
    DEFAULT_ADMIN_EMAIL = (os.getenv("DEFAULT_ADMIN_EMAIL") or "siya1@gmail.com").strip().lower()
    DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD") or "Siyabonga@2"

    # --- Uploads: PDF "Update Profile Picture" view: JPG, PNG or WEBP, max 2MB
    MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # the photo itself: 2MB
    MAX_CONTENT_LENGTH = MAX_UPLOAD_BYTES + 512 * 1024  # whole request, leaves room for form fields
    ALLOWED_IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp"})
    UPLOAD_FOLDER = str(BASE_DIR / "app" / "static" / "uploads")
    # Admin > Products: clothing can have several photos, each up to MAX_UPLOAD_BYTES, in one request.
    MAX_PRODUCT_PHOTOS = 6
    ADMIN_PRODUCT_MAX_CONTENT_LENGTH = MAX_PRODUCT_PHOTOS * MAX_UPLOAD_BYTES + 512 * 1024

    # --- Passwords / identity ------------------------------------------------
    BCRYPT_LOG_ROUNDS = _env_int("BCRYPT_LOG_ROUNDS", 12)
    # Secret used to fingerprint SA ID numbers (HMAC-SHA256). Changing it makes
    # existing fingerprints unmatchable, so keep it stable once users exist.
    ID_HASH_PEPPER = os.getenv("ID_HASH_PEPPER") or None  # falls back to SECRET_KEY
    SETTINGS_ENCRYPTION_KEY = (
        os.getenv("SETTINGS_ENCRYPTION_KEY") or None
    )  # encrypts API keys saved in the admin portal; falls back to SECRET_KEY

    # --- Registration / profile choices (Step 3 and 5) -------------------------
    GENDER_CHOICES = ("Male", "Female", "Other", "Prefer not to say")
    RACE_CHOICES = ("African", "Coloured", "Indian/Asian", "White", "Other", "Prefer not to say")
    PREFERENCE_TYPES = ("Dietary", "Brand", "Store", "Category")
    MAX_PREFERENCES_PER_USER = 50

    # --- Abuse protection ---------------------------------------------------------
    # Flask-Limiter caps requests per minute on the sign-in/registration forms and on /api. With several gunicorn
    # workers point REDIS_URL at Redis so they share the counters; "memory://" counts per worker.
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI") or _env_first("REDIS_URL") or "memory://"
    RATELIMIT_HEADERS_ENABLED = True  # X-RateLimit-* and Retry-After on limited routes
    RATELIMIT_AUTH = os.getenv("RATELIMIT_AUTH") or "20 per minute"  # POST /login, /register (per IP)
    RATELIMIT_API = os.getenv("RATELIMIT_API") or "240 per minute"  # every /api route (per student, else per IP)
    # The counters below are the app's own, for things Flask-Limiter cannot see: failed sign-ins per e-mail,
    # and costly product searches per student (in-memory, per process).
    LOGIN_FAILURE_LIMIT = 8  # failed sign-ins allowed ...
    LOGIN_FAILURE_WINDOW = 15 * 60  # ... per this many seconds, per IP and per e-mail
    GEOCODE_REQUEST_LIMIT = 30  # lookups allowed ...
    GEOCODE_REQUEST_WINDOW = 60  # ... per this many seconds, per IP
    SEARCH_REQUEST_LIMIT = 40  # product searches allowed ...
    SEARCH_REQUEST_WINDOW = 60  # ... per this many seconds, per student

    # --- Geocoding: Nominatim (OpenStreetMap). Usage policy: identify the app in
    #     the User-Agent, max 1 request/second, cache results, no autocomplete. ----
    NOMINATIM_BASE_URL = os.getenv("NOMINATIM_BASE_URL") or "https://nominatim.openstreetmap.org"
    NOMINATIM_USER_AGENT = os.getenv("NOMINATIM_USER_AGENT") or (
        "AISmartShopper/1.0 (student budgeting app, Durban; set NOMINATIM_USER_AGENT with a contact e-mail)"
    )
    NOMINATIM_MIN_INTERVAL = 1.0  # seconds between outbound requests
    NOMINATIM_TIMEOUT = 6  # seconds
    GEOCODE_COUNTRY_CODES = "za"
    # left, top, right, bottom (lon/lat): soft bias towards greater Durban
    GEOCODE_VIEWBOX = (30.60, -29.55, 31.40, -30.20)
    GEOCODE_CACHE_TTL = 24 * 60 * 60
    # Look an address up on the server when the browser did not send a pin (JavaScript off, or the
    # address was edited after picking). Best effort: a failure just leaves the coordinates empty.
    GEOCODE_ON_SAVE = True

    # --- Caching (Flask-Caching). 15 minutes, as required for retail data ----------
    CACHE_REDIS_URL = _env_first("CACHE_REDIS_URL", "REDIS_URL")
    CACHE_TYPE = os.getenv("CACHE_TYPE") or ("RedisCache" if CACHE_REDIS_URL else "SimpleCache")
    CACHE_DEFAULT_TIMEOUT = 15 * 60

    # --- External API call log (retail + geocoding) --------------------------------
    API_LOG_FILE = os.getenv("API_LOG_FILE") or str(BASE_DIR / "app" / "logs" / "api_calls.log")

    # --- Retail & price data (Step 6) -------------------------------------------
    # "mock" (offline, deterministic - dev/tests), "loyaltyhub", "buyly", "apify" or "parsebot"
    RETAIL_PROVIDER = os.getenv("RETAIL_PROVIDER") or "mock"
    RETAIL_CACHE_TTL = 15 * 60
    RETAIL_SETTINGS_RECHECK_SECONDS = 30  # how often each worker re-reads the admin portal's data-source settings
    RETAIL_DEFAULT_RADIUS_KM = 15
    APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")
    APIFY_BASE_URL = "https://api.apify.com/v2"
    APIFY_RUN_TIMEOUT = _env_int("APIFY_RUN_TIMEOUT", 120)  # seconds to wait for an actor run
    APIFY_PAGE_SIZE = 100
    # retailer -> {"actor": "<user~actor-name or id>", "input": {...}}. See services/retail_api.py.
    APIFY_ACTORS_JSON = os.getenv("APIFY_ACTORS_JSON")
    PARSEBOT_API_KEY = os.getenv("PARSEBOT_API_KEY")
    PARSEBOT_BASE_URL = "https://api.parse.bot"
    # retailer -> scraper id, e.g. {"woolworths": "3c08f360-..."}
    PARSEBOT_SCRAPERS_JSON = os.getenv("PARSEBOT_SCRAPERS_JSON")
    # LoyaltyHub SA Grocery Price API: prices, barcodes and product images for the big chains. Refresh regularly without
    # exhausting the free key's 100 calls a month.
    LOYALTYHUB_API_KEY = os.getenv("LOYALTYHUB_API_KEY")
    LOYALTYHUB_BASE_URL = os.getenv("LOYALTYHUB_BASE_URL") or "https://loyaltyhub.co.za/api/v1"
    LOYALTYHUB_CACHE_TTL = _env_int("LOYALTYHUB_CACHE_TTL", 24 * 60 * 60)
    LOYALTYHUB_PAGE_SIZE = _env_int("LOYALTYHUB_PAGE_SIZE", 100)  # rows per search (one call; the API allows up to 500)
    # Buyly (closed beta, key by application). The response format is not public, see services/retail_api.py.
    BUYLY_API_KEY = os.getenv("BUYLY_API_KEY")
    BUYLY_BASE_URL = os.getenv("BUYLY_BASE_URL") or "https://api.buyly.co.za/v1"
    BUYLY_CACHE_TTL = _env_int("BUYLY_CACHE_TTL", 60 * 60)

    # --- Stores API (Step 7) ---------------------------------------------------------
    STORE_DEFAULT_RADIUS_KM = 10
    STORE_MAX_RADIUS_KM = 50

    # --- Brand colours (keep in sync with static/css/app.css :root) ---------
    THEME = {"primary": "#2C666E", "surface": "#F0EDEE"}

    # --- Money (all prices are in ZAR) ------------------------------------
    CURRENCY_CODE = "ZAR"
    CURRENCY_SYMBOL = "R"

    # NSFAS meal-allowance REFERENCE (PDF: Budget View section D, Set Budget
    # View section A). This is a WARNING threshold only: exceeding it must
    # never block the student from creating or saving a budget.
    NSFAS_MONTHLY_ALLOWANCE = Decimal("1750.00")

    # --- Budget categories (PDF: Set Budget View section C) ----------------
    # "Combined" is mutually exclusive with the individual categories
    # (PDF: Set Budget View section D).
    BUDGET_CATEGORIES = ("Grocery", "Toiletries", "Clothes", "Electronics")
    COMBINED_BUDGET_CATEGORY = "Combined"

    # --- Dashboard / Budget view sizing (PDF) -----------------------------
    DASHBOARD_RECENT_ITEM_COUNT = 4  # "four most recently purchased high-cost items"
    SPENDING_HISTORY_MONTHS = 6  # "Six-Month Spending Graph"
    HIGH_COST_ITEM_THRESHOLD = Decimal("500.00")  # "Recently Bought (High Cost Items)": UnitCost >= R500 ...
    HIGH_COST_FALLBACK_DAYS = 60  # ... or the most expensive purchases of the last 60 days
    MAX_BUDGET_AMOUNT = Decimal("1000000.00")  # sanity cap per budget (the column holds up to R99 999 999.99)
    MAX_ITEM_QUANTITY = 99

    # --- Search / shopping list (Steps 11-12) -------------------------------------
    SEARCH_MAX_RADIUS_KM = 15  # "Distance (slider up to 15 km)"
    SEARCH_PAGE_SIZE = 24
    ALTERNATIVES_TTL = 15 * 60  # re-check a list item's cheaper alternative after this many seconds
    RECENT_PURCHASE_DAYS = 30  # "Recently purchased" tag; older purchases are tagged "Lastly purchased"

    # --- Recommendation engine (Step 15) --------------------------------------------------
    # --- Notifications (Step 17) ---
    NOTIFICATION_BUDGET_THRESHOLDS = (50, 75, 90, 100)  # % of the budget used
    NOTIFICATION_PRICE_DROP = 0.10  # a list item this much cheaper (10%) is announced
    NOTIFICATION_PRICE_CHECK_TTL = 900  # seconds between price look-ups per student (they call the retail API)
    NOTIFICATION_MAX_NEW_ALTERNATIVES = 3  # cheaper-alternative notices per check, so one visit cannot flood the bell
    NOTIFICATION_BELL_LIMIT = 8  # rows in the bell dropdown
    RECOMMENDER_WINDOW_DAYS = 90  # purchases older than this do not count
    RECOMMENDER_PROMO_DROP = 0.10  # a price this far under the store's usual price counts as a promotion
    RECOMMENDER_WEIGHTS = {"content": 0.4, "collaborative": 0.4, "price": 0.2}

    # --- Weather (Open-Meteo: free, no API key) - cached 30 minutes ---------------------
    OPEN_METEO_BASE_URL = _env_first("OPEN_METEO_BASE_URL", "OPEN_METEO_URL") or "https://api.open-meteo.com/v1"
    WEATHER_CACHE_TTL = 30 * 60
    WEATHER_TIMEOUT = 5

    # --- Shopping route (OSRM public demo server: fair use only, see services/routing.py) ---
    OSRM_BASE_URL = os.getenv("OSRM_BASE_URL") or "https://router.project-osrm.org"
    OSRM_TIMEOUT = 8
    ROUTE_CACHE_TTL = 30 * 60
    ROUTE_RETURN_HOME = True  # the trip ends back at the student's home

    # --- Maps: Durban is the default centre (Leaflet + OpenStreetMap) -------
    DEFAULT_MAP_CENTER = {"lat": -29.8587, "lng": 31.0218}
    DEFAULT_MAP_ZOOM = 12
    DEFAULT_SEARCH_RADIUS_KM = 5  # PDF Search view: "Within 5 km" filter
    MAP_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    MAP_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

    # --- Security headers (Flask-Talisman) and static files (WhiteNoise) --------------------
    FORCE_HTTPS = _env_bool("FORCE_HTTPS")  # production turns this on; the proxy must send X-Forwarded-Proto
    HSTS_SECONDS = _env_int("HSTS_SECONDS", 31536000)
    USE_WHITENOISE = _env_bool("USE_WHITENOISE")  # production serves /static itself, no nginx needed
    WHITENOISE_MAX_AGE = 24 * 60 * 60  # static URLs carry ?v=<file time>, so a day is safe
    # What the pages may load. Scripts: our own files and the CDNs in base.html (inline scripts need the per-request nonce).
    # Styles keep 'unsafe-inline' because Bootstrap/Leaflet and a few templates set style attributes.
    # Images: any https host, because retail APIs return product pictures from many domains.
    CSP = {
        "default-src": "'self'",
        "script-src": ["'self'", "https://cdn.jsdelivr.net", "https://unpkg.com"],
        "style-src": [
            "'self'",
            "'unsafe-inline'",
            "https://cdn.jsdelivr.net",
            "https://unpkg.com",
            "https://fonts.googleapis.com",
        ],
        "font-src": ["'self'", "https://cdn.jsdelivr.net", "https://fonts.gstatic.com", "data:"],
        "img-src": ["'self'", "data:", "blob:", "https:"],
        "connect-src": "'self'",
        "object-src": "'none'",
        "base-uri": "'self'",
        "form-action": ["'self'", "https://login.microsoftonline.com"],
        "frame-ancestors": "'none'",
    }

    @classmethod
    def init_app(cls, app) -> None:  # pragma: no cover - hook for subclasses
        pass


class DevConfig(BaseConfig):
    DEBUG = True
    # Relative SQLite paths resolve inside Flask's instance/ folder.
    SQLALCHEMY_DATABASE_URI = os.getenv("DEV_DATABASE_URL") or "sqlite:///dev.db"


class TestConfig(BaseConfig):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    BCRYPT_LOG_ROUNDS = 4  # fast hashing in tests
    RATELIMIT_ENABLED = False
    CACHE_TYPE = "NullCache"
    NOMINATIM_MIN_INTERVAL = 0
    GEOCODE_ON_SAVE = False  # tests turn it on where they mock Nominatim
    RETAIL_PROVIDER = "mock"
    APIFY_API_TOKEN = None
    PARSEBOT_API_KEY = None
    LOYALTYHUB_API_KEY = None
    BUYLY_API_KEY = None
    API_LOG_FILE = str(BASE_DIR / "instance" / "test_api_calls.log")
    MICROSOFT_CLIENT_ID = None
    MICROSOFT_CLIENT_SECRET = None


class ProdConfig(BaseConfig):
    SQLALCHEMY_DATABASE_URI = _normalise_postgres_url(os.getenv("DATABASE_URL"))
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    FORCE_HTTPS = _env_bool("FORCE_HTTPS", True)
    # Cookies are Secure whenever the site is served over https (turn FORCE_HTTPS off only for a local http trial run).
    SESSION_COOKIE_SECURE = FORCE_HTTPS
    REMEMBER_COOKIE_SECURE = FORCE_HTTPS
    PREFERRED_URL_SCHEME = "https" if FORCE_HTTPS else "http"
    USE_WHITENOISE = _env_bool("USE_WHITENOISE", True)
    TRUST_PROXY = _env_bool("TRUST_PROXY", True)  # Heroku, Render, Railway and Fly all sit behind a proxy

    @classmethod
    def init_app(cls, app) -> None:
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            raise RuntimeError("DATABASE_URL must be set (PostgreSQL) in production.")
        key = (app.config["SECRET_KEY"] or "").strip()
        if key.lower() in _PLACEHOLDER_KEYS or len(key) < MIN_SECRET_KEY_LENGTH:
            raise RuntimeError(
                "SECRET_KEY must be a strong random value of at least 32 characters in production. "
                'Make one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )


config_by_name = {
    "development": DevConfig,
    "production": ProdConfig,
    "testing": TestConfig,
}
DEFAULT_CONFIG_NAME = "development"
