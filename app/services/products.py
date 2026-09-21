"""Product helpers shared by search, the shopping list and recommendations.

* :func:`offer_to_card` - the JSON shape of one product-at-one-store, as the search cards, the details modal and
  the recommendation strip all use it.
* :func:`similar_offers` - other products that could stand in for one (same category and pack size, same key
  words once the brand is left out). It relies on names that start with the brand ("Clover Full Cream Milk 2L"),
  which is how the South African retailers name things; a live provider with different naming just finds fewer.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.services.retail_api import Product, RetailProvider
from app.utils.money import money

_SIZE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s?(kg|g|ml|l|cl|s|pack|pk)\b", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z]{3,}")


def size_token(name: str | None) -> str | None:
    """``"2L"`` from ``"Clover Full Cream Milk 2L"`` (lower-cased, comma as decimal point), or ``None``."""
    match = _SIZE.search(name or "")
    return f"{match.group(1).replace(',', '.')}{match.group(2)}".casefold() if match else None


def key_words(name: str | None) -> list[str]:
    """The words of a product name apart from its pack size and its brand (taken to be the first word)."""
    words = _WORD.findall(_SIZE.sub(" ", name or ""))
    return words[1:]


def similar_offers(
    provider: RetailProvider,
    name: str,
    category: str | None,
    lat: float,
    lng: float,
    radius_km: float,
    exclude_barcode: str | None = None,
) -> list[Product]:
    """In-stock offers for products similar to ``name`` (not the product itself), cheapest first."""
    size = size_token(name)
    words = key_words(name)
    if size is None or not words:
        return []
    found = []
    for offer in provider.search_products(" ".join(words), lat, lng, radius_km, category):
        if not offer.in_stock or (exclude_barcode and offer.barcode == exclude_barcode):
            continue
        if size_token(offer.name) != size:
            continue
        if category and offer.category and offer.category != category:
            continue
        found.append(offer)
    return sorted(found, key=lambda o: (o.price, o.store_name, o.name))


def offer_key(barcode: str | None, store_id: str | None, store_name: str | None, name: str | None) -> str:
    """Identity of one product at one store, the same for the search card and the list row."""
    return f"{barcode or (name or '').casefold()}@{store_id or store_name or ''}"


def in_list_quantities(items) -> dict[tuple[str, str], int]:
    """``{(barcode or lower-cased name, store name): quantity}`` for the items of the active list."""
    return {
        ((item.BarCode or (item.ItemName or "").casefold()), item.StoreName or ""): item.ItemQuantity for item in items
    }


def offer_to_card(offer: Product, badges=(), quantities: dict | None = None, tag: str | None = None) -> dict:
    ident = offer.barcode or offer.name.casefold()
    return {
        "key": offer_key(offer.barcode, offer.store_id, offer.store_name, offer.name),
        "barcode": offer.barcode,
        "name": offer.name,
        "brand": offer.brand,
        "retailer": offer.retailer,
        "price": str(money(offer.price)),
        "image_url": offer.image_url,
        "category": offer.category,
        "store_id": offer.store_id,
        "store_name": offer.store_name,
        "store_address": offer.store_address,
        "store_lat": offer.store_lat,
        "store_lng": offer.store_lng,
        "distance_km": None if offer.distance_km is None else round(offer.distance_km, 1),
        "in_stock": offer.in_stock,
        "badges": list(badges),
        "tag": tag,
        "in_list_qty": (quantities or {}).get((ident, offer.store_name or ""), 0),
    }


def cheapest_in_stock(offers) -> Product | None:
    offers = list(offers)
    if not offers:
        return None
    return min([o for o in offers if o.in_stock] or offers, key=lambda o: (o.price, o.store_name))


def savings_text(viewed: Product, offers) -> dict:
    """The details modal's "Save R6 by going to Checkers" line, for ``viewed`` among ``offers`` (same barcode)."""
    from app.utils.formatters import format_zar

    in_stock = [o for o in offers if o.in_stock]
    if len(in_stock) < 2:
        return {"kind": "single", "text": None, "amount": "0.00"}
    cheapest = min(in_stock, key=lambda o: (o.price, o.store_name))
    diff = money(viewed.price) - money(cheapest.price)
    if diff > Decimal("0"):
        place = cheapest.retailer or cheapest.store_name
        return {
            "kind": "save",
            "amount": str(diff),
            "store_name": cheapest.store_name,
            "text": f"Save {format_zar(diff)} by going to {place}",
        }
    return {"kind": "cheapest", "amount": "0.00", "text": "This is the cheapest price near you."}
