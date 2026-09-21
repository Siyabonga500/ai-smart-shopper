"""What the Search view knows about the student's tastes (the RECOMMENDED badge on a product card).

Two inputs, both the student's own data: what they bought before (items on completed shopping lists) and their
saved preferences (Preferred Store / Brand / Category from the profile page). The "Recommended for you" carousel
itself is built by ``services/recommender.py`` (Step 15).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models import ListItem, Preference
from app.services.retail_api import Product
from app.utils.dates import as_utc, utcnow


@dataclass(frozen=True)
class PreferenceProfile:
    stores: frozenset
    brands: frozenset
    categories: frozenset


@dataclass(frozen=True)
class PurchaseSummary:
    key: str  # barcode, or the lower-cased name when the product had no barcode
    barcode: str | None
    name: str
    category: str | None
    last_purchased: datetime
    times: int


def preference_profile(user_id: str) -> PreferenceProfile:
    found = {"store": set(), "brand": set(), "category": set()}
    for pref in db.session.scalars(select(Preference).where(Preference.UserId == user_id)):
        bucket = found.get((pref.PreferenceType or "").strip().casefold())
        if bucket is not None:
            bucket.add((pref.PreferenceValue or "").strip().casefold())
    return PreferenceProfile(frozenset(found["store"]), frozenset(found["brand"]), frozenset(found["category"]))


def purchase_history(user_id: str) -> list[PurchaseSummary]:
    """Distinct products the student has bought, most recently bought first."""
    rows = db.session.scalars(
        select(ListItem)
        .where(ListItem.UserId == user_id, ListItem.IsPurchased.is_(True))
        .order_by(ListItem.PurchasedDate.desc())
    ).all()
    merged: dict[str, PurchaseSummary] = {}
    for item in rows:
        key = item.BarCode or (item.ItemName or "").casefold()
        when = as_utc(item.PurchasedDate) or utcnow()
        if key in merged:
            old = merged[key]
            merged[key] = PurchaseSummary(
                key, old.barcode, old.name, old.category, max(old.last_purchased, when), old.times + 1
            )
        else:
            merged[key] = PurchaseSummary(key, item.BarCode, item.ItemName, item.Category, when, 1)
    return sorted(merged.values(), key=lambda p: p.last_purchased, reverse=True)


def is_recommended(offer: Product, profile: PreferenceProfile, purchased_keys: set[str], is_cheapest: bool) -> bool:
    """RECOMMENDED badge: bought before; a preferred brand; or the cheapest price at a preferred store."""
    if (offer.barcode or offer.name.casefold()) in purchased_keys:
        return True
    if offer.brand and offer.brand.casefold() in profile.brands:
        return True
    return bool(is_cheapest and offer.retailer and offer.retailer.casefold() in profile.stores)


def purchase_tag(last_purchased: datetime, now: datetime | None = None) -> str:
    days = current_app.config["RECENT_PURCHASE_DAYS"]
    age = (now or utcnow()) - last_purchased
    return "Recently purchased" if age <= timedelta(days=days) else "Lastly purchased"
