"""Store management for the admin portal (Step 16): the ``stores`` table (name, brand, address, position, hours)."""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import or_, select

from app.extensions import db
from app.models import Store
from app.utils.pagination import Page, like_pattern, paginate


class StoreError(ValueError):
    """A store change that is refused; the message is safe to show."""


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "store"


def unique_slug(name: str, ignore_id: str | None = None) -> str:
    base = slugify(name)
    slug, n = base, 1
    while True:
        query = select(Store.StoreId).where(Store.Slug == slug)
        if ignore_id:
            query = query.where(Store.StoreId != ignore_id)
        if not db.session.scalar(query):
            return slug
        n += 1
        slug = f"{base[:90]}-{n}"


def get(store_id: str) -> Store | None:
    return db.session.get(Store, store_id)


def brands() -> list[str]:
    return list(db.session.scalars(select(Store.Brand).distinct().order_by(Store.Brand)))


def missing_seeds() -> int:
    """How many of the known Durban branches (supermarkets and clothing shops) are not in the table yet."""
    from app.data.durban_stores import ALL_STORES

    have = set(db.session.scalars(select(Store.Slug)))
    return sum(1 for seed in ALL_STORES if seed.slug not in have)


def add_known_stores() -> int:
    """Add the known Durban branches that are missing (never changes a store an admin edited). The caller commits."""
    from app.data.durban_stores import ALL_STORES

    have = set(db.session.scalars(select(Store.Slug)))
    added = 0
    for seed in ALL_STORES:
        if seed.slug in have:
            continue
        db.session.add(
            Store(
                Slug=seed.slug,
                Name=seed.name,
                Brand=seed.brand,
                Suburb=seed.suburb,
                Address=seed.address,
                Latitude=seed.lat,
                Longitude=seed.lng,
                LocationSource=seed.source,
                Phone=seed.phone,
                OpeningHours=seed.hours,
                StoreType=seed.kind,
            )
        )
        added += 1
    return added


def search(
    query: str | None = None, brand: str | None = None, page=1, per_page: int = 20, store_type: str | None = None
) -> Page:
    statement = select(Store)
    text = (query or "").strip()
    if text:
        pattern = like_pattern(text)
        statement = statement.where(
            or_(
                Store.Name.ilike(pattern, escape="\\"),
                Store.Address.ilike(pattern, escape="\\"),
                Store.Suburb.ilike(pattern, escape="\\"),
            )
        )
    if brand:
        statement = statement.where(Store.Brand == brand)
    if store_type:
        statement = statement.where(Store.StoreType == store_type)
    return paginate(statement.order_by(Store.Brand, Store.Name), page, per_page)


def snapshot(store: Store) -> dict:
    return {
        "name": store.Name,
        "brand": store.Brand,
        "suburb": store.Suburb,
        "address": store.Address,
        "lat": store.Latitude,
        "lng": store.Longitude,
        "hours": store.OpeningHours,
        "phone": store.Phone,
        "location_source": store.LocationSource,
        "store_type": store.StoreType,
    }


def apply(store: Store, data: dict) -> Store:
    """Copy the validated form ``data`` onto ``store``. The caller adds/commits it with the audit row."""
    store.Name, store.Brand, store.Suburb, store.Address = data["name"], data["brand"], data["suburb"], data["address"]
    store.Latitude, store.Longitude = data["lat"], data["lng"]
    store.OpeningHours, store.Phone = data["hours"], data["phone"]
    store.LocationSource = data["location_source"]
    store.StoreType = data.get("store_type") or store.StoreType or "grocery"
    if not store.Slug:
        store.Slug = unique_slug(store.Name)
    return store
