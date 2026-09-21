"""Flask extension instances, created unbound and attached in ``create_app``.

Keeping them here (rather than in ``app/__init__.py``) avoids circular imports
between the app factory, the models and the blueprints.
"""

import sqlite3

from flask_bcrypt import Bcrypt
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.utils.ratelimit import SlidingWindowLimiter

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
bcrypt = Bcrypt()
csrf = CSRFProtect()
cache = Cache()


def user_or_ip() -> str:
    """Rate-limit key: the signed-in student (so a campus Wi-Fi does not share one bucket), otherwise the address."""
    if current_user.is_authenticated:
        return f"user:{current_user.UserId}"
    return get_remote_address()


# Limits and storage come from config (RATELIMIT_*); the routes that carry limits are wired in routes/__init__.py.
limiter = Limiter(key_func=get_remote_address)

# Limits are overwritten from config in create_app; these are only the defaults.
login_limiter = SlidingWindowLimiter(limit=8, window_seconds=15 * 60)
geocode_limiter = SlidingWindowLimiter(limit=30, window_seconds=60)
search_limiter = SlidingWindowLimiter(
    limit=40, window_seconds=60
)  # product searches per student (they cost money on live APIs)


@event.listens_for(Engine, "connect")
def _enforce_sqlite_foreign_keys(dbapi_connection, _connection_record):
    """SQLite ignores foreign keys (and ON DELETE CASCADE) unless told otherwise.

    Turning them on makes the dev database behave like PostgreSQL in production.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "warning"
