"""Product search (Step 11): query + filters + sorting over whatever the retail provider returns.

The provider does the searching (:meth:`RetailProvider.search_products`, cached for 15 minutes). This module
turns its offers into the cards the page shows:

* filters: category (asked of the provider), price range, chosen stores, distance (kilometres);
* badges: CHEAPEST on the lowest-priced offer of a product that is sold in more than one store (same barcode = same
  product), RECOMMENDED from the student's history and preferences (see recommendations.py);
* sorting: lowest price, highest price, closest store, A-Z, store name;
* paging: ``SEARCH_PAGE_SIZE`` cards per page.

CHEAPEST is worked out over every in-stock offer inside the distance limit, *before* the price and store
filters, so a card never claims to be the cheapest just because a cheaper store was filtered out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models import Store, User
from app.services.products import in_list_quantities, offer_to_card
from app.services.recommendations import is_recommended, preference_profile, purchase_history
from app.services.retail_api import Product, RetailProvider, group_by_barcode
from app.services.shopping import student_center
from app.utils.geo import distance_km
from app.utils.money import parse_amount

SORTS = ("lowest", "highest", "closest", "az", "store")
SORT_LABELS = {
    "lowest": "Lowest price",
    "highest": "Highest price",
    "closest": "Closest store",
    "az": "A to Z",
    "store": "Store name",
}
MAX_QUERY_LENGTH = 100
MAX_STORE_FILTERS = 40


@dataclass
class SearchParams:
    q: str = ""
    category: str | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    store_ids: list[str] = field(default_factory=list)
    radius_km: float = 5.0
    sort: str = "lowest"
    page: int = 1


@dataclass
class SearchResult:
    cards: list[dict]
    total: int
    page: int
    pages: int
    radius_km: float
    center: dict

    def to_dict(self, query: str = "") -> dict:
        return {
            "query": query,
            "total": self.total,
            "page": self.page,
            "pages": self.pages,
            "radius_km": self.radius_km,
            "center": self.center,
            "results": self.cards,
        }


def parse_search_params(args) -> tuple[SearchParams, dict]:
    """Read and validate the query string of ``/api/search``. Returns the params and ``{field: message}`` errors."""
    cfg = current_app.config
    errors: dict[str, str] = {}
    params = SearchParams(radius_km=float(cfg["DEFAULT_SEARCH_RADIUS_KM"]))

    params.q = " ".join((args.get("q") or "").split())
    if len(params.q) > MAX_QUERY_LENGTH:
        errors["q"] = f"Search for {MAX_QUERY_LENGTH} characters or fewer."

    category = (args.get("category") or "").strip()
    if category:
        match = next((c for c in cfg["BUDGET_CATEGORIES"] if c.casefold() == category.casefold()), None)
        if match is None:
            errors["category"] = "Choose Grocery, Toiletries, Clothes or Electronics."
        params.category = match

    for name, attr in (("min_price", "min_price"), ("max_price", "max_price")):
        raw = (args.get(name) or "").strip()
        if raw:
            amount = parse_amount(raw)
            if amount is None or amount < 0:
                errors[name] = "Enter a price in rand, e.g. 50."
            else:
                setattr(params, attr, amount)
    if params.min_price is not None and params.max_price is not None and params.min_price > params.max_price:
        errors["max_price"] = "The highest price must not be below the lowest price."

    raw_radius = (args.get("radius") or "").strip()
    if raw_radius:
        try:
            radius = float(raw_radius)
        except ValueError:
            radius = float("nan")
        if not math.isfinite(radius) or radius <= 0:
            errors["radius"] = "Distance must be a number of kilometres."
        else:
            params.radius_km = min(radius, float(cfg["SEARCH_MAX_RADIUS_KM"]))

    sort = (args.get("sort") or "lowest").strip().lower()
    if sort not in SORTS:
        errors["sort"] = "Unknown sort order."
    else:
        params.sort = sort

    params.store_ids = [s.strip() for s in (args.get("stores") or "").split(",") if s.strip()][:MAX_STORE_FILTERS]

    try:
        params.page = max(1, int(args.get("page") or 1))
    except ValueError:
        errors["page"] = "Page must be a number."
    return params, errors


def _store_matchers(store_ids: list[str]) -> tuple[set[str], set[str]]:
    """Slugs and names of the chosen stores (offers carry either, depending on the provider)."""
    if not store_ids:
        return set(), set()
    stores = db.session.scalars(select(Store).where(Store.StoreId.in_(store_ids))).all()
    return {s.Slug for s in stores}, {s.Name for s in stores}


def _sort_key(sort: str):
    far = float("inf")
    if sort == "highest":
        return lambda c: (-Decimal(c["price"]), c["name"].casefold())
    if sort == "closest":
        return lambda c: (
            c["distance_km"] if c["distance_km"] is not None else far,
            Decimal(c["price"]),
            c["name"].casefold(),
        )
    if sort == "az":
        return lambda c: (c["name"].casefold(), Decimal(c["price"]), c["store_name"] or "")
    if sort == "store":
        return lambda c: ((c["store_name"] or "").casefold(), Decimal(c["price"]), c["name"].casefold())
    return lambda c: (Decimal(c["price"]), c["name"].casefold(), c["store_name"] or "")


def with_distance(offer: Product, center: dict) -> Product:
    """``offer`` with its distance worked out from ``center`` (the student's own address).

    Always recomputed when the store's position is known: a cached provider answer may have been worked out for a
    neighbour up to a kilometre away."""
    if offer.store_lat is None or offer.store_lng is None:
        return offer
    from dataclasses import replace

    return replace(
        offer, distance_km=round(distance_km(center["lat"], center["lng"], offer.store_lat, offer.store_lng), 2)
    )


def run_search(provider: RetailProvider, user: User, params: SearchParams, active_items=()) -> SearchResult:
    """Search, filter, badge, sort and page. Raises the provider's ``RetailAPIError`` if it cannot answer."""
    cfg = current_app.config
    center = student_center(user)
    if not params.q and not params.category:
        return SearchResult([], 0, 1, 1, params.radius_km, center)

    offers = [
        with_distance(o, center)
        for o in provider.search_products(params.q, center["lat"], center["lng"], params.radius_km, params.category)
    ]
    offers = [o for o in offers if o.distance_km is None or o.distance_km <= params.radius_km]

    # Badges are decided on the whole distance-limited result, before the price and store filters.
    cheapest_keys: set[tuple[str, str]] = set()
    for group in group_by_barcode(offers):
        priced = [o for o in group.offers if o.in_stock]
        if group.barcode and len(priced) > 1:
            lowest = min(o.price for o in priced)
            cheapest_keys.update((o.barcode, o.store_name) for o in priced if o.price == lowest)

    slugs, names = _store_matchers(params.store_ids)
    shown = []
    for offer in offers:
        if params.min_price is not None and offer.price < params.min_price:
            continue
        if params.max_price is not None and offer.price > params.max_price:
            continue
        if params.store_ids and offer.store_id not in slugs and offer.store_name not in names:
            continue
        shown.append(offer)

    profile = preference_profile(user.UserId)
    purchased = {p.key for p in purchase_history(user.UserId)}
    quantities = in_list_quantities(active_items)
    cards = []
    for offer in shown:
        is_cheapest = (offer.barcode, offer.store_name) in cheapest_keys
        badges = []
        if is_cheapest:
            badges.append("CHEAPEST")
        if offer.in_stock and is_recommended(offer, profile, purchased, is_cheapest):
            badges.append("RECOMMENDED")
        cards.append(offer_to_card(offer, badges, quantities))

    cards.sort(key=_sort_key(params.sort))
    size = cfg["SEARCH_PAGE_SIZE"]
    pages = max(1, math.ceil(len(cards) / size))
    page = min(params.page, pages)
    return SearchResult(cards[(page - 1) * size : page * size], len(cards), page, pages, params.radius_km, center)
