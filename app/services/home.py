"""The public home page: a few products per category for visitors who have not signed in.

Each card is one product with its lowest in-stock price around Durban and how many stores have it. The answers come
from the same retail provider students use (cached), so the page costs no extra calls once warm.
"""

from __future__ import annotations

from flask import current_app

from app.services.http import ExternalAPIError
from app.services.retail_api import RetailConfigError, get_retail_provider, group_by_barcode
from app.utils.geo import default_map_center
from app.utils.money import money

PER_CATEGORY = 8

# Money tips for the footer of every page (one a day).
TIPS = (
    "Compare before you go: the same milk can cost R5 more two streets away.",
    "Buy store brands for staples like rice, maize meal and pasta — they are often 20% cheaper.",
    "Plan one trip for the week. Fewer trips means less taxi fare and fewer impulse buys.",
    "Tick items off as you shop, so nothing extra lands in the trolley.",
    "Keep R100 of your allowance aside for the end of the month.",
    "Bulk-buy toiletries with a friend and split the cost.",
    "A shopping list you stick to is the cheapest thing in the shop.",
)


def tip_of_the_day(today) -> str:
    return TIPS[today.toordinal() % len(TIPS)]


def featured_products(per_category: int = PER_CATEGORY) -> list[dict]:
    """``[{"category", "products": [card, ...]}, ...]`` for the home page. Never raises: a failing source shows nothing."""
    center = default_map_center()
    radius = current_app.config["SEARCH_MAX_RADIUS_KM"]
    sections = []
    try:
        provider = get_retail_provider()
    except RetailConfigError:
        return []
    for category in current_app.config["BUDGET_CATEGORIES"]:
        try:
            offers = provider.search_products("", center["lat"], center["lng"], radius, category)
        except (ExternalAPIError, RetailConfigError):
            current_app.logger.warning("Home page products for %s could not be loaded", category, exc_info=True)
            continue
        cards = []
        for group in group_by_barcode(offers):
            in_stock = [o for o in group.offers if o.in_stock]
            if not in_stock:
                continue
            best = min(in_stock, key=lambda o: (o.price, o.store_name or ""))
            cards.append(
                {
                    "name": best.name,
                    "brand": best.brand,
                    "barcode": best.barcode,
                    "price": money(best.price),
                    "image_url": best.image_url,
                    "retailer": best.retailer,
                    "store_name": best.store_name,
                    "store_count": len({o.store_name for o in in_stock}),
                    "stock": "Low stock" if best.stock_status == "low_stock" else "In stock",
                }
            )
            if len(cards) >= per_category:
                break
        if cards:
            sections.append({"category": category, "products": cards})
    return sections
