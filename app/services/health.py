"""Health report used by ``GET /health`` (load balancers, uptime monitors) and ``flask healthcheck`` (Docker).

The database decides whether the app is *healthy*: without it nothing works, so /health answers 503. A cache
that is down only makes the app slower, so it shows as ``degraded`` but still answers 200. Nothing here
includes error text, because /health is public.
"""

import logging
import uuid

from flask import current_app
from sqlalchemy import text

from app.extensions import cache, db
from app.utils.dates import utcnow

log = logging.getLogger(__name__)


def check_database() -> str:
    try:
        db.session.execute(text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001 - any failure means "down"
        db.session.rollback()
        log.exception("Health check: the database is unreachable")
        return "unavailable"


def check_cache() -> str:
    """``ok`` when a value can be stored and read back, ``disabled`` for the no-op test cache, else ``unavailable``."""
    if current_app.config.get("CACHE_TYPE") == "NullCache":
        return "disabled"
    key, value = f"health:{uuid.uuid4().hex}", "1"
    try:
        cache.set(key, value, timeout=15)
        found = cache.get(key) == value
        cache.delete(key)
        return "ok" if found else "unavailable"
    except Exception:  # noqa: BLE001
        log.exception("Health check: the cache is unreachable")
        return "unavailable"


def last_api_sync() -> str | None:
    """ISO time of the last successful or failed "Sync now" of the active data source, if there was one."""
    from app.services import integrations

    try:
        when = integrations.last_sync()
    except Exception:  # noqa: BLE001 - a missing table must not break the health page
        db.session.rollback()
        return None
    return when.isoformat(timespec="seconds") if when else None


def report() -> tuple[dict, bool]:
    """``(payload, healthy)``. ``healthy`` is False only when the database is down."""
    database = check_database()
    cache_status = check_cache()
    healthy = database == "ok"
    if not healthy:
        status = "unhealthy"
    elif cache_status == "unavailable":
        status = "degraded"
    else:
        status = "ok"
    payload = {
        "status": status,
        "app": current_app.config["APP_NAME"],
        "environment": current_app.config["CONFIG_NAME"],
        "database": database,
        "cache": cache_status,
        "last_api_sync": last_api_sync() if healthy else None,
        "timestamp": utcnow().isoformat(timespec="seconds"),
    }
    return payload, healthy
