"""Data-source integrations (Step 16): which retail source the app uses, its API key, syncing and the call log.

Sources: ``loyaltyhub``, ``buyly``, ``apify``, ``parsebot`` and ``mock`` (the built-in catalogue). Exactly one is *active*. Until an admin
chooses, the active source is the ``RETAIL_PROVIDER`` environment setting, and API keys come from the environment
(``LOYALTYHUB_API_KEY``, ``BUYLY_API_KEY``, ``APIFY_API_TOKEN``, ``PARSEBOT_API_KEY``). A key typed into the portal is stored encrypted and wins over the
environment one. ``get_retail_provider()`` reads all of this through :func:`effective_config`.

"Sync now" does not copy a product catalogue (this app asks the source live and caches answers for 15 minutes).
It clears the cached answers, so the next searches are fresh, and makes one test search to prove the source and its
key work, recording when that happened and how it went.
"""

from __future__ import annotations

import json
import logging
import time
from collections import ChainMap, Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import IntegrationSetting
from app.models.admin import SOURCES
from app.services import retail_api
from app.services.retail_api import RetailAPIError, RetailConfigError
from app.utils.dates import as_utc, to_sast, today_sast, utcnow
from app.utils.geo import default_map_center
from app.utils.secrets import decrypt_secret, encrypt_secret, mask_secret

log = logging.getLogger(__name__)

SYNC_PROBE_QUERY = "milk"
KEY_MIN, KEY_MAX = 8, 512
LOG_BACKUPS = 3  # RotatingFileHandler keeps api_calls.log.1 ... .3 (see services/http.py)


class IntegrationError(ValueError):
    """An integration change that cannot be made; the message is safe to show the admin."""


@dataclass(frozen=True)
class SourceInfo:
    key: str
    label: str
    description: str
    config_key: str | None  # the app.config / environment name of its API key (None: no key needed)
    key_label: str = "API key"


SOURCE_INFO = {
    "loyaltyhub": SourceInfo(
        "loyaltyhub",
        "LoyaltyHub",
        "South African grocery prices with product images and barcodes (Checkers, Pick n Pay, Shoprite, Woolworths, SPAR ...). "
        "Recommended. The free key allows 100 calls a month, so answers refresh every 15 minutes.",
        "LOYALTYHUB_API_KEY",
        "API key",
    ),
    "buyly": SourceInfo(
        "buyly",
        "Buyly",
        "Buyly's product and price API (closed beta: you need a key from Buyly). Its response format is not public, "
        "so this connector is untested against the live service.",
        "BUYLY_API_KEY",
        "API key",
    ),
    "apify": SourceInfo(
        "apify",
        "Apify",
        "Live supermarket prices from the Apify actors listed in APIFY_ACTORS_JSON.",
        "APIFY_API_TOKEN",
        "API token",
    ),
    "parsebot": SourceInfo(
        "parsebot",
        "Parse.bot",
        "Live supermarket prices from the Parse.bot scrapers listed in PARSEBOT_SCRAPERS_JSON.",
        "PARSEBOT_API_KEY",
        "API key",
    ),
    "mock": SourceInfo(
        "mock",
        "Mock data",
        "The built-in catalogue of about 200 products. Needs no key; use it for demos and testing.",
        None,
    ),
}
assert tuple(SOURCE_INFO) == SOURCES


# --------------------------------------------------------------------------------------------- settings
def _rows() -> dict[str, IntegrationSetting]:
    return {row.Source: row for row in db.session.scalars(select(IntegrationSetting))}


def ensure_rows() -> dict[str, IntegrationSetting]:
    """One row per source, created on first use. Flushes; the caller commits."""
    rows = _rows()
    for source in SOURCES:
        if source not in rows:
            rows[source] = IntegrationSetting(Source=source, IsActive=False)
            db.session.add(rows[source])
    db.session.flush()
    return rows


def effective_config(base):
    """``base`` (the app config) with the admin portal's active source and API keys laid over it."""
    try:
        rows = _rows()
    except SQLAlchemyError:  # tables missing (a fresh checkout): use the environment only
        db.session.rollback()
        return base
    overrides = {}
    active = next((source for source, row in rows.items() if row.IsActive), None)
    if active:
        overrides["RETAIL_PROVIDER"] = active
    for source, info in SOURCE_INFO.items():
        row = rows.get(source)
        key = decrypt_secret(row.ApiKeyEncrypted) if row and info.config_key else None
        if key:
            overrides[info.config_key] = key
    return ChainMap(overrides, base) if overrides else base


def active_source() -> str:
    cfg = effective_config(current_app.config)
    return (cfg.get("RETAIL_PROVIDER") or "mock").strip().casefold()


def _key_state(source: str, row: IntegrationSetting | None) -> tuple[str | None, str]:
    """``(key in use, where it comes from)``: ``"portal"``, ``"environment"``, ``"stored but unreadable"`` or ``"none"``."""
    info = SOURCE_INFO[source]
    if not info.config_key:
        return None, "not needed"
    if row and row.ApiKeyEncrypted:
        key = decrypt_secret(row.ApiKeyEncrypted)
        if key:
            return key, "admin portal"
        stored_unreadable = True
    else:
        stored_unreadable = False
    env = current_app.config.get(info.config_key)
    if env:
        return env, "environment"
    return None, "stored but unreadable" if stored_unreadable else "none"


def overview() -> list[dict]:
    """What the Integrations page shows, one dict per source."""
    rows = _rows()
    active = active_source()
    result = []
    for source, info in SOURCE_INFO.items():
        row = rows.get(source)
        key, origin = _key_state(source, row)
        result.append(
            {
                "key": source,
                "label": info.label,
                "description": info.description,
                "needs_key": bool(info.config_key),
                "key_label": info.key_label,
                "is_active": source == active,
                "has_key": bool(key),
                "key_origin": origin,
                "masked_key": mask_secret(key),
                "can_remove_key": bool(row and row.ApiKeyEncrypted),
                "last_sync_on": row.LastSyncOn if row else None,
                "last_sync_status": row.LastSyncStatus if row else None,
                "last_sync_message": row.LastSyncMessage if row else None,
            }
        )
    return result


def _check_source(source: str) -> SourceInfo:
    if source not in SOURCE_INFO:
        raise IntegrationError("Unknown data source.")
    return SOURCE_INFO[source]


def invalidate() -> None:
    """Make the next ``get_retail_provider()`` rebuild from the settings, and forget cached answers."""
    retail_api.reset_retail_provider()
    retail_api.invalidate_retail_cache()


def activate(source: str) -> str:
    """Make ``source`` the active one. Returns the source it replaced. The caller commits (with the audit row)."""
    info = _check_source(source)
    previous = active_source()
    rows = ensure_rows()
    if info.config_key and not _key_state(source, rows[source])[0]:
        raise IntegrationError(f"Add {info.label}'s {info.key_label.lower()} before making it the active source.")
    for name, row in rows.items():
        row.IsActive = name == source
    return previous


def commit_and_refresh() -> None:
    db.session.commit()
    invalidate()


def set_key(source: str, key: str | None) -> None:
    info = _check_source(source)
    if not info.config_key:
        raise IntegrationError(f"{info.label} does not use an API key.")
    key = (key or "").strip()
    if not KEY_MIN <= len(key) <= KEY_MAX:
        raise IntegrationError(f"The {info.key_label.lower()} must be between {KEY_MIN} and {KEY_MAX} characters.")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in key):
        raise IntegrationError(
            f"The {info.key_label.lower()} cannot contain spaces or line breaks. Paste just the key."
        )
    ensure_rows()[source].ApiKeyEncrypted = encrypt_secret(key)


def remove_key(source: str) -> None:
    info = _check_source(source)
    rows = ensure_rows()
    row = rows[source]
    if not row.ApiKeyEncrypted:
        raise IntegrationError("No key is saved in the portal for this source.")
    env = current_app.config.get(info.config_key or "")
    if row.IsActive and not env:
        raise IntegrationError(f"{info.label} is the active source. Switch to another source before removing its key.")
    row.ApiKeyEncrypted = None


def _provider_for(source: str):
    """A fresh, uncached provider for ``source`` using its effective key (used by "Sync now")."""
    cfg = effective_config(current_app.config)
    return retail_api.build_provider(ChainMap({"RETAIL_PROVIDER": source}, cfg))


def sync_now(source: str) -> IntegrationSetting:
    """Clear cached answers and run one test search against ``source``. Records the outcome; the caller commits."""
    _check_source(source)
    row = ensure_rows()[source]
    centre = default_map_center()
    started = time.perf_counter()
    try:
        offers = _provider_for(source).search_products(
            SYNC_PROBE_QUERY, centre["lat"], centre["lng"], current_app.config["RETAIL_DEFAULT_RADIUS_KM"]
        )
        status, message = (
            "ok",
            f"Test search for '{SYNC_PROBE_QUERY}' found {len(offers)} offers in {time.perf_counter() - started:.1f}s.",
        )
    except (RetailAPIError, RetailConfigError) as exc:
        status, message = "error", str(exc)[:300]
    except Exception:  # never let a source crash the admin page
        log.exception("Sync of %s failed unexpectedly", source)
        status, message = "error", "The source failed unexpectedly. Check the server log."
    retail_api.invalidate_retail_cache()
    row.LastSyncOn, row.LastSyncStatus, row.LastSyncMessage = utcnow(), status, message
    return row


def last_sync() -> datetime | None:
    """The most recent sync of the active source (falls back to any source), for the /health page."""
    rows = _rows()
    active = rows.get(active_source())
    if active is not None and active.LastSyncOn:
        return as_utc(active.LastSyncOn)
    return as_utc(max((as_utc(row.LastSyncOn) for row in rows.values() if row.LastSyncOn), default=None))


# ------------------------------------------------------------------------------------------------ API log
def log_paths() -> list[Path]:
    base = Path(current_app.config["API_LOG_FILE"])
    return [base] + [base.with_name(f"{base.name}.{n}") for n in range(1, LOG_BACKUPS + 1)]


def _parse(line: str) -> dict | None:
    try:
        record = json.loads(line)
        when = datetime.strptime(record["ts"], "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    status = record.get("status")
    record["when"] = when
    record["ok"] = isinstance(status, int) and 200 <= status < 400 and not record.get("error")
    return record


def iter_calls():
    """Every logged call, newest first (the rotating log files are read newest to oldest)."""
    for path in log_paths():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            record = _parse(line) if line.strip() else None
            if record is not None:
                yield record


def recent_calls(
    limit: int = 100,
    service: str | None = None,
    outcome: str | None = None,
    day: date | None = None,
    scan: int = 20_000,
) -> list[dict]:
    """Newest calls first, optionally only one service, only ``ok``/``error`` calls, or one SAST day."""
    found = []
    for index, record in enumerate(iter_calls()):
        if index >= scan or len(found) >= limit:
            break
        if service and record.get("service") != service:
            continue
        if outcome == "ok" and not record["ok"]:
            continue
        if outcome == "error" and record["ok"]:
            continue
        if day and to_sast(record["when"]).date() != day:
            continue
        found.append(record)
    return found


def calls_per_day(days: int = 7, today: date | None = None) -> list[dict]:
    """``[{"date": date, "ok": n, "error": n}]`` for the last ``days`` SAST days, oldest first."""
    today = today or today_sast()
    wanted = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    tally: dict[date, Counter] = {day: Counter() for day in wanted}
    for record in iter_calls():
        day = to_sast(record["when"]).date()
        if day in tally:
            tally[day]["ok" if record["ok"] else "error"] += 1
    return [{"date": day, "ok": tally[day]["ok"], "error": tally[day]["error"]} for day in wanted]


def calls_today() -> int:
    """Logged requests today (SAST). Each retry is a request, so a call that needed three tries counts three."""
    day = today_sast()
    total = 0
    for record in iter_calls():
        record_day = to_sast(record["when"]).date()
        if record_day == day:
            total += 1
        elif record_day < day - timedelta(days=1):
            break  # newest first: nothing older than yesterday matters
    return total


def services_in_log() -> list[str]:
    return sorted({record.get("service") for _, record in zip(range(5000), iter_calls()) if record.get("service")})
