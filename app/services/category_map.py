"""Category mappings (Step 16): what a retail API calls a category -> the categories this app budgets by.

Apify and Parse.bot return whatever category text the retailer uses ("Fresh Milk & Dairy", "Health & Beauty").
An admin maps each of those names to one of Grocery, Toiletries, Clothes, Electronics or Combined and, from then
on, every product the retail provider returns carries the mapped category (the provider applies the table each
time it answers, so a change takes effect within a minute without clearing any cache).

Raw names are matched ignoring upper/lower case and repeated spaces.
"""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import cache, db
from app.models import CategoryMapping
from app.models.admin import MAPPED_CATEGORIES
from app.utils.dates import utcnow
from app.utils.pagination import Page, like_pattern, paginate

CACHE_KEY = "category-map"
CACHE_SECONDS = 60
RAW_MAX = 150
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class MappingError(ValueError):
    """A category mapping that cannot be saved; the message is safe to show the admin."""


def normalise_key(raw: str | None) -> str:
    """``"  Fresh   Milk & DAIRY "`` -> ``"fresh milk & dairy"``."""
    text = unicodedata.normalize("NFC", raw or "")
    return re.sub(r"\s+", " ", text).strip().casefold()


def clean(raw: str | None, mapped: str | None) -> tuple[str, str, str]:
    """``(raw as typed, key, mapped)`` or :class:`MappingError`."""
    typed = re.sub(r"\s+", " ", unicodedata.normalize("NFC", raw or "")).strip()
    if not typed:
        raise MappingError("Enter the category name the retail API uses.")
    if len(typed) > RAW_MAX:
        raise MappingError(f"The category name can be at most {RAW_MAX} characters.")
    if _CONTROL.search(typed):
        raise MappingError("The category name contains characters that are not allowed.")
    target = next((c for c in MAPPED_CATEGORIES if c.casefold() == (mapped or "").strip().casefold()), None)
    if target is None:
        raise MappingError("Choose one of: " + ", ".join(MAPPED_CATEGORIES) + ".")
    return typed, normalise_key(typed), target


def invalidate() -> None:
    cache.delete(CACHE_KEY)


def table() -> dict[str, tuple[str, str]]:
    """``{key: (raw name, mapped category)}``: read by the retail provider; kept for ``CACHE_SECONDS``."""
    hit = cache.get(CACHE_KEY)
    if hit is not None:
        return hit
    try:
        rows = db.session.execute(
            select(CategoryMapping.RawKey, CategoryMapping.RawCategory, CategoryMapping.MappedCategory)
        ).all()
    except SQLAlchemyError:  # tables not created yet (a fresh checkout): behave as "no mappings"
        db.session.rollback()
        return {}
    value = {key: (raw, mapped) for key, raw, mapped in rows}
    cache.set(CACHE_KEY, value, timeout=CACHE_SECONDS)
    return value


def get(mapping_id: str) -> CategoryMapping | None:
    return db.session.get(CategoryMapping, mapping_id)


def search(query: str | None = None, category: str | None = None, page=1, per_page: int = 25) -> Page:
    statement = select(CategoryMapping)
    if query and query.strip():
        statement = statement.where(CategoryMapping.RawCategory.ilike(like_pattern(query.strip()), escape="\\"))
    if category:
        statement = statement.where(CategoryMapping.MappedCategory == category)
    return paginate(statement.order_by(CategoryMapping.MappedCategory, CategoryMapping.RawKey), page, per_page)


def counts() -> dict[str, int]:
    found = dict(
        db.session.execute(
            select(CategoryMapping.MappedCategory, func.count()).group_by(CategoryMapping.MappedCategory)
        ).all()
    )
    return {name: found.get(name, 0) for name in MAPPED_CATEGORIES}


def create(raw: str, mapped: str) -> CategoryMapping:
    """Add a mapping. The caller commits (with the audit row)."""
    typed, key, target = clean(raw, mapped)
    if db.session.scalar(select(CategoryMapping.MappingId).where(CategoryMapping.RawKey == key)):
        raise MappingError("That category is already mapped. Edit the existing mapping instead.")
    row = CategoryMapping(RawCategory=typed, RawKey=key, MappedCategory=target)
    db.session.add(row)
    return row


def update(row: CategoryMapping, raw: str, mapped: str) -> CategoryMapping:
    typed, key, target = clean(raw, mapped)
    clash = db.session.scalar(
        select(CategoryMapping.MappingId).where(
            CategoryMapping.RawKey == key, CategoryMapping.MappingId != row.MappingId
        )
    )
    if clash:
        raise MappingError("Another mapping already uses that category name.")
    row.RawCategory, row.RawKey, row.MappedCategory = typed, key, target
    row.UpdatedOn = utcnow()
    return row


def commit_and_refresh() -> None:
    """Commit, turning a race on the unique key into the same friendly error, and drop the cached table."""
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise MappingError("That category is already mapped.") from None
    invalidate()
