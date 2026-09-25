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
    dietary: frozenset = frozenset()

    @property
    def empty(self) -> bool:
        return not (self.stores or self.brands or self.categories or self.dietary)


# Words in a product name that satisfy a dietary preference (anything else is matched by the word itself).
DIETARY_WORDS = {
    "vegetarian": ("vegetarian", "veggie", "soya", "plant"),
    "vegan": ("vegan", "plant based", "plant-based"),
    "halaal": ("halaal", "halal"),
    "halal": ("halaal", "halal"),
    "kosher": ("kosher",),
    "gluten free": ("gluten free", "gluten-free"),
    "lactose free": ("lactose free", "lactose-free"),
    "sugar free": ("sugar free", "sugar-free", "no sugar", "no added sugar"),
    "low fat": ("low fat", "low-fat", "lite", "light", "fat free"),
}
DIETARY_CHOICES = ("Vegetarian", "Vegan", "Halaal", "Kosher", "Gluten free", "Lactose free", "Sugar free", "Low fat")
CATEGORY_ALIASES = {"clothing": "clothes"}


def preference_reasons(offer: Product, profile: PreferenceProfile) -> list[str]:
    """Which of the student's saved preferences this offer matches, e.g. ``["Your store: Checkers"]``."""
    if profile.empty:
        return []
    reasons = []
    places = {(offer.retailer or "").casefold(), (offer.store_name or "").casefold()}
    store = next((s for s in profile.stores if s and any(s == p or s in p for p in places if p)), None)
    if store:
        reasons.append(f"Your store: {offer.retailer or offer.store_name}")
    if offer.brand and offer.brand.casefold() in profile.brands:
        reasons.append(f"Your brand: {offer.brand}")
    category = (offer.category or "").casefold()
    if category and category in {CATEGORY_ALIASES.get(c, c) for c in profile.categories}:
        reasons.append(f"Your category: {offer.category}")
    name = offer.name.casefold()
    for diet in sorted(profile.dietary):
        if any(word in name for word in DIETARY_WORDS.get(diet, (diet,))):
            reasons.append(f"Dietary: {diet.capitalize()}")
    return reasons


@dataclass(frozen=True)
class PurchaseSummary:
    key: str  # barcode, or the lower-cased name when the product had no barcode
    barcode: str | None
    name: str
    category: str | None
    last_purchased: datetime
    times: int


def preference_profile(user_id: str) -> PreferenceProfile:
    found = {"store": set(), "brand": set(), "category": set(), "dietary": set()}
    for pref in db.session.scalars(select(Preference).where(Preference.UserId == user_id)):
        bucket = found.get((pref.PreferenceType or "").strip().casefold())
        if bucket is not None:
            bucket.add((pref.PreferenceValue or "").strip().casefold())
    return PreferenceProfile(
        frozenset(found["store"]), frozenset(found["brand"]), frozenset(found["category"]), frozenset(found["dietary"])
    )


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


def preference_suggestions() -> dict[str, list[str]]:
    """What the "Add a preference" box suggests for each type: real stores, brands and categories from the app."""
    from app.data.durban_stores import ALL_STORES
    from app.data.mock_products import CATALOGUE
    from app.models import CatalogueProduct, Store

    stores = {seed.brand for seed in ALL_STORES} | set(db.session.scalars(select(Store.Brand).distinct()))
    brands = {item.brand for item in CATALOGUE if item.brand not in ("Fresh", "Generic")}
    brands |= {b for b in db.session.scalars(select(CatalogueProduct.Brand).distinct()) if b}
    return {
        "Dietary": list(DIETARY_CHOICES),
        "Brand": sorted(brands, key=str.casefold),
        "Store": sorted(stores, key=str.casefold),
        "Category": [("Clothing" if c == "Clothes" else c) for c in current_app.config["BUDGET_CATEGORIES"]],
    }
