"""Retail & price data (Step 6): one interface, interchangeable sources.

    MockProvider      offline, deterministic, ~200 South African products. The default, and what the tests use.
                      Its barcodes are made up and its pictures are a placeholder: use a live source for real images.
    LoyaltyHubProvider  LoyaltyHub's SA Grocery Price API: prices, barcodes and product images for the big chains.
    BuylyProvider     Buyly's product API (closed beta; its response format is not public, so it is mapped tolerantly).
    ApifyProvider     Apify actors that scrape Checkers, Pick n Pay, Shoprite, Woolworths and SPAR.
    ParseBotProvider  Parse.bot scrapers (a Woolworths scraper is built in; more can be added in config).

Pick one with ``RETAIL_PROVIDER=mock|loyaltyhub|buyly|apify|parsebot`` and get it from :func:`get_retail_provider`, which wraps
it in a 15-minute cache. Every provider returns the same :class:`Product`, so the rest of the app never
needs to know where a price came from.

What the live providers can and cannot do (read this before switching them on)
-----------------------------------------------------------------------------
* Apify actors are third-party scrapers with their own input and output fields, which nobody can guess.
  You give the actor and its input template in ``APIFY_ACTORS_JSON`` (see ``.env.example``); the output is
  read with a tolerant mapper that recognises the usual field names and can be overridden per retailer.
* Scraped prices are usually the *online* price for the retailer, not one particular branch's shelf price, and
  often carry no barcode. Products are attached to the nearest branch of that retailer in the ``stores`` table
  (Step 7) so distance sorting works, and barcode grouping only works for rows that really contain a barcode.
* None of these sources publish price history, so ``get_price_history`` is only real for the mock provider.
* LoyaltyHub is the one source that returns a product picture, price and barcode in a documented, stable format.
    Its free key allows only 100 calls a month, so its answers are cached briefly (``LOYALTYHUB_CACHE_TTL``).
* Buyly is a closed beta: the endpoints below come from its public page, the response fields are guessed with the
  same tolerant mapper the scrapers use, and it has never been run against a real Buyly key.
* This code was written against the documented HTTP APIs and is tested with mocked responses. It has not been run
  against the live services, which need your own credentials and credits.

Every external call goes through :mod:`app.services.http`: retry with back-off on 429/5xx, and one JSON line per
call in ``app/logs/api_calls.log`` (URLs are logged without credentials; keys travel in headers).
"""

import hashlib
import json
import logging
import random
import re
import time
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from flask import current_app, has_app_context

from app.data.durban_stores import DURBAN_STORES
from app.data.mock_products import CATALOGUE, CatalogueItem
from app.extensions import cache
from app.services.http import ExternalAPIError, request_with_retry
from app.utils.geo import default_map_center, distance_km

logger = logging.getLogger(__name__)

RETAILERS = ("Checkers", "Pick n Pay", "Shoprite", "Woolworths", "SPAR")
PLACEHOLDER_IMAGE = "/static/img/product-placeholder.svg"
# The built-in catalogue has invented barcodes, so no real photo exists for it: show a picture of the aisle instead.
CATEGORY_PLACEHOLDERS = {
    name: f"/static/img/placeholders/{name.lower()}.svg" for name in ("Grocery", "Toiletries", "Clothes", "Electronics")
}

_RETAILER_ALIASES = {
    "checkers": "Checkers",
    "checkers sixty60": "Checkers",
    "sixty60": "Checkers",
    "pnp": "Pick n Pay",
    "pick n pay": "Pick n Pay",
    "pick and pay": "Pick n Pay",
    "picknpay": "Pick n Pay",
    "shoprite": "Shoprite",
    "woolworths": "Woolworths",
    "woolies": "Woolworths",
    "spar": "SPAR",
    "superspar": "SPAR",
}


_FALLBACK_CENTER = {"lat": -29.8587, "lng": 31.0218}  # Durban, for use outside an app context


def _default_center() -> dict:
    return default_map_center() if has_app_context() else dict(_FALLBACK_CENTER)


class RetailAPIError(ExternalAPIError):
    """A retail data source failed or answered with something unusable."""


class RetailConfigError(RuntimeError):
    """The chosen provider is missing the settings it needs."""


def canonical_retailer(name: str | None) -> str | None:
    return _RETAILER_ALIASES.get((name or "").strip().casefold())


# ---------------------------------------------------------------------------------------------- data types
@dataclass(frozen=True)
class Product:
    """One product as offered by one store (the same product at two stores is two ``Product`` rows)."""

    name: str
    barcode: str | None
    price: Decimal  # ZAR
    image_url: str | None
    store_name: str
    store_address: str | None
    store_lat: float | None
    store_lng: float | None
    category: str | None
    in_stock: bool = True
    # Extras beyond the brief, used for grouping, sorting and display:
    brand: str | None = None
    retailer: str | None = None  # "Checkers", "Pick n Pay", ...
    store_id: str | None = None  # stores.Slug (mock) - what get_price_history() expects
    distance_km: float | None = None

    def to_dict(self) -> dict:
        data = {name: getattr(self, name) for name in self.__dataclass_fields__}
        data["price"] = str(self.price.quantize(Decimal("0.01")))  # exact, JSON-safe
        if self.distance_km is not None:
            data["distance_km"] = round(self.distance_km, 2)
        return data


@dataclass(frozen=True)
class RetailStore:
    id: str
    name: str
    brand: str
    address: str
    lat: float
    lng: float
    phone: str | None = None
    opening_hours: str | None = None
    distance_km: float | None = None


@dataclass(frozen=True)
class PricePoint:
    date: date
    price: Decimal


@dataclass(frozen=True)
class ProductGroup:
    """Offers for the same barcode, cheapest first - the unit of price comparison."""

    barcode: str | None
    name: str
    offers: tuple[Product, ...]

    @property
    def cheapest(self) -> Product:
        in_stock = [offer for offer in self.offers if offer.in_stock]
        return min(in_stock or self.offers, key=lambda offer: offer.price)

    @property
    def savings(self) -> Decimal:
        """Difference between the dearest and cheapest in-stock offer (0 for a single offer)."""
        prices = [offer.price for offer in self.offers if offer.in_stock]
        return max(prices) - min(prices) if len(prices) > 1 else Decimal("0.00")


def group_by_barcode(products) -> list[ProductGroup]:
    """Group offers that share a barcode (same barcode = same product). Rows without a barcode stay alone.

    Groups keep the order in which each barcode first appeared; offers inside a group are cheapest first.
    """
    grouped: dict[str, list[Product]] = {}
    singles: list[tuple[int, Product]] = []
    order: dict[str, int] = {}
    for index, product in enumerate(products):
        if product.barcode:
            grouped.setdefault(product.barcode, []).append(product)
            order.setdefault(product.barcode, index)
        else:
            singles.append((index, product))

    groups = [
        (order[code], ProductGroup(code, offers[0].name, tuple(sorted(offers, key=lambda p: (p.price, p.store_name)))))
        for code, offers in grouped.items()
    ]
    groups += [(index, ProductGroup(None, product.name, (product,))) for index, product in singles]
    return [group for _, group in sorted(groups, key=lambda pair: pair[0])]


# ---------------------------------------------------------------------------------------------- interface
class RetailProvider(ABC):
    """What the app needs from a retail data source."""

    name = "base"
    cache_ttl: int | None = None  # seconds to keep answers; ``None`` = the app's RETAIL_CACHE_TTL

    @abstractmethod
    def search_products(
        self, query: str, lat: float, lng: float, radius_km: float = 15, category: str | None = None
    ) -> list[Product]:
        """Offers matching ``query`` at stores within ``radius_km`` of the point (cheapest first per product)."""

    @abstractmethod
    def get_offers_by_barcode(
        self, barcode: str, lat: float | None = None, lng: float | None = None, radius_km: float = 15
    ) -> list[Product]:
        """Every store's offer for one barcode, cheapest first."""

    def get_product_by_barcode(
        self, barcode: str, lat: float | None = None, lng: float | None = None, radius_km: float = 15
    ) -> Product | None:
        """The cheapest in-stock offer for ``barcode`` (any offer if none is in stock), or ``None``."""
        offers = self.get_offers_by_barcode(barcode, lat, lng, radius_km)
        if not offers:
            return None
        return min((o for o in offers if o.in_stock) or offers, key=lambda offer: offer.price)

    def get_stores_near(self, lat: float, lng: float, radius_km: float = 15) -> list[RetailStore]:
        """Branches around a point, nearest first. Default: our own ``stores`` table (Step 7)."""
        from app.services.stores import stores_near

        return [
            RetailStore(
                s.StoreId,
                s.Name,
                s.Brand,
                s.Address,
                s.Latitude,
                s.Longitude,
                s.Phone,
                s.OpeningHours,
                round(d, 2),
            )
            for s, d in stores_near(lat, lng, radius_km)
        ]

    @abstractmethod
    def get_price_history(self, barcode: str, store_id: str) -> list[PricePoint]:
        """Past prices of one product at one store, oldest first (may be empty)."""


# ---------------------------------------------------------------------------------------------- mock
# Typical price level of each retailer relative to Checkers (Woolworths dearest, Shoprite cheapest).
_RETAILER_FACTOR = {"Shoprite": 0.96, "Checkers": 1.00, "Pick n Pay": 1.04, "SPAR": 1.07, "Woolworths": 1.18}
_HISTORY_POINTS = 12
_BY_BARCODE = {item.barcode: item for item in CATALOGUE}


def _unit(*parts: str) -> float:
    """Deterministic pseudo-random number in [0, 1) derived from the parts."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _to_ninety_nine(amount: Decimal) -> Decimal:
    """Round to the nearest rand and end in .99, the way shelf prices do (R29.99)."""
    whole = amount.to_integral_value(rounding=ROUND_HALF_UP)
    return max(whole - Decimal("0.01"), Decimal("0.99"))


class MockProvider(RetailProvider):
    """Offline provider over :data:`app.data.mock_products.CATALOGUE`. Same input, same output, always."""

    name = "mock"

    # -- pricing rules -----------------------------------------------------------------------------------
    @staticmethod
    def retailer_price(item: CatalogueItem, retailer: str) -> Decimal:
        factor = _RETAILER_FACTOR[retailer] * (0.96 + 0.08 * _unit(item.barcode, retailer, "price"))
        return _to_ninety_nine(Decimal(item.price) * Decimal(str(round(factor, 4))))

    @staticmethod
    def retailer_lists(item: CatalogueItem, retailer: str) -> bool:
        """Does this retailer range the product at all? Woolworths carries roughly half of the brands."""
        if item.brand in ("Fresh", "Generic"):
            return True
        if retailer == "Woolworths":
            return _unit(item.brand, "woolworths-range") < 0.55
        return _unit(item.barcode, retailer, "range") < 0.93

    @staticmethod
    def retailer_in_stock(item: CatalogueItem, retailer: str) -> bool:
        return _unit(item.barcode, retailer, "stock") >= 0.06

    # -- geography ---------------------------------------------------------------------------------------------
    @staticmethod
    def _nearest_branches(lat: float, lng: float, radius_km: float) -> dict[str, tuple]:
        """Nearest branch of each retailer within the radius: ``{retailer: (seed, distance_km)}``."""
        best: dict[str, tuple] = {}
        for seed in DURBAN_STORES:
            distance = distance_km(lat, lng, seed.lat, seed.lng)
            if distance <= radius_km and (seed.brand not in best or distance < best[seed.brand][1]):
                best[seed.brand] = (seed, distance)
        return best

    def _offers(self, item: CatalogueItem, branches: dict[str, tuple]) -> list[Product]:
        offers = []
        for retailer, (seed, distance) in branches.items():
            if not self.retailer_lists(item, retailer):
                continue
            offers.append(
                Product(
                    name=item.name,
                    barcode=item.barcode,
                    price=self.retailer_price(item, retailer),
                    image_url=CATEGORY_PLACEHOLDERS.get(item.category, PLACEHOLDER_IMAGE),
                    store_name=seed.name,
                    store_address=seed.address,
                    store_lat=seed.lat,
                    store_lng=seed.lng,
                    category=item.category,
                    in_stock=self.retailer_in_stock(item, retailer),
                    brand=item.brand,
                    retailer=retailer,
                    store_id=seed.slug,
                    distance_km=round(distance, 2),
                )
            )
        return sorted(offers, key=lambda offer: (offer.price, offer.store_name))

    @staticmethod
    def _center(lat, lng) -> tuple[float, float]:
        if lat is None or lng is None:
            default = _default_center()
            return default["lat"], default["lng"]
        return lat, lng

    # -- interface ------------------------------------------------------------------------------------------------
    def search_products(self, query, lat, lng, radius_km=15, category=None, limit=200):
        tokens = re.findall(r"\w+", (query or "").casefold())
        wanted = (category or "").strip().casefold()
        if not tokens and not wanted:
            # Default browse: show a broad slice of the catalogue instead of an empty result.
            tokens = []
            wanted = ""

        lat, lng = self._center(lat, lng)
        branches = self._nearest_branches(lat, lng, radius_km)
        if not branches:
            return []

        matches = []
        for item in CATALOGUE:
            if wanted and item.category.casefold() != wanted:
                continue
            haystack = f"{item.name} {item.brand} {item.category}".casefold()
            if not tokens or all(token in haystack for token in tokens):
                name = item.name.casefold()
                rank = (
                    0 if tokens and name.startswith(tokens[0]) else 1,
                    min((name.find(token) for token in tokens if token in name), default=99),
                    name,
                )
                matches.append((rank, item))

        results: list[Product] = []
        for _, item in sorted(matches, key=lambda pair: pair[0]):
            results.extend(self._offers(item, branches))
            if len(results) >= limit:
                break
        return results[:limit]

    def get_offers_by_barcode(self, barcode, lat=None, lng=None, radius_km=15):
        item = _BY_BARCODE.get((barcode or "").strip())
        if item is None:
            return []
        lat, lng = self._center(lat, lng)
        return self._offers(item, self._nearest_branches(lat, lng, radius_km))

    def get_stores_near(self, lat, lng, radius_km=15):
        found = []
        for seed in DURBAN_STORES:
            distance = distance_km(lat, lng, seed.lat, seed.lng)
            if distance <= radius_km:
                found.append(
                    RetailStore(
                        seed.slug,
                        seed.name,
                        seed.brand,
                        seed.address,
                        seed.lat,
                        seed.lng,
                        seed.phone,
                        seed.hours,
                        round(distance, 2),
                    )
                )
        return sorted(found, key=lambda store: (store.distance_km, store.name))

    def get_price_history(self, barcode, store_id):
        item = _BY_BARCODE.get((barcode or "").strip())
        seed = next((s for s in DURBAN_STORES if s.slug == store_id), None)
        if item is None or seed is None:
            return []
        rng = random.Random(int(hashlib.sha256(f"{barcode}|{store_id}".encode()).hexdigest()[:12], 16))
        price = current = self.retailer_price(item, seed.brand)
        reverse = [current]  # newest first, then walk backwards in time
        for _ in range(_HISTORY_POINTS - 1):
            step = Decimal(str(round(rng.uniform(-0.06, 0.06), 4)))
            if rng.random() < 0.12:
                step -= Decimal("0.10")  # an occasional promotion
            price = _to_ninety_nine(price * (Decimal(1) + step))
            reverse.append(price)
        today = date.today()
        return [
            PricePoint(today - timedelta(weeks=index), value) for index, value in reversed(list(enumerate(reverse)))
        ]


# ---------------------------------------------------------------------------------------------- parsing helpers
_NAME_KEYS = ("name", "title", "productName", "product_name", "description")
_PRICE_KEYS = (
    "price",
    "currentPrice",
    "current_price",
    "salePrice",
    "sale_price",
    "sellingPrice",
    "priceValue",
    "price_value",
)
_BARCODE_KEYS = ("barcode", "ean", "gtin", "gtin13", "gtin14", "upc", "barCode", "bar_code")
_IMAGE_KEYS = ("image", "imageUrl", "image_url", "imageURL", "thumbnail", "img", "images.0", "image.0")
_CATEGORY_KEYS = ("category", "categoryName", "category_name", "department")
_BRAND_KEYS = ("brand", "brandName", "brand_name")
_STOCK_KEYS = ("inStock", "in_stock", "available", "isAvailable", "availability")
_CONTAINER_KEYS = ("products", "items", "results", "data")


def _dig(raw, path: str):
    """``_dig({"a": {"b": 1}}, "a.b") == 1``; numeric parts index into lists."""
    value = raw
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, (list, tuple)) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
        if value is None:
            return None
    return value


def _first(raw, keys, override: str | None = None):
    for key in ([override] if override else []) + list(keys):
        value = _dig(raw, key)
        if value not in (None, "", [], {}):
            return value
    return None


def parse_price(value, in_cents: bool = False) -> Decimal | None:
    """A positive 2-dp ``Decimal`` from ``29.99``, ``"R 1 049,00"``, ``{"amount": 29.99}`` ...; ``None`` if unusable."""
    if isinstance(value, dict):
        value = _first(value, ("value", "amount", "current", "now", "price", "selling"))
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        amount = Decimal(str(value))
    else:
        text = re.sub(r"[^\d.,]", "", str(value).replace(" ", " "))
        if not text:
            return None
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):  # 1.049,50 - the last separator is the decimal one
                text = text.replace(".", "").replace(",", ".")
            else:  # 1,049.50
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(",", ".") if re.search(r",\d{1,2}$", text) else text.replace(",", "")
        try:
            amount = Decimal(text)
        except InvalidOperation:
            return None
    if in_cents:
        amount = amount / 100
    if not amount.is_finite() or amount <= 0:
        return None
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def clean_barcode(value) -> str | None:
    """Digits only, and only if the length is a real GTIN (8, 12, 13 or 14). Retailer SKUs are not barcodes."""
    digits = re.sub(r"\D", "", str(value)) if value not in (None, "") else ""
    return digits if len(digits) in (8, 12, 13, 14) else None


def _to_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() not in (
            "false",
            "0",
            "no",
            "out of stock",
            "outofstock",
            "unavailable",
            "sold out",
        )
    return bool(value)


def clean_image_url(value) -> str | None:
    """An ``https`` picture address, or ``None``. ``//cdn/x.jpg`` and ``http://`` are upgraded (the page's CSP only allows https)."""
    if isinstance(value, dict):
        value = _first(value, ("url", "src", "large", "medium", "small"))
    if not isinstance(value, str):
        return None
    url = value.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.lower().startswith("http://"):
        url = "https://" + url[7:]
    return url if url.lower().startswith("https://") and len(url) <= 1000 and not re.search(r"\s", url) else None


def normalise_product(
    raw,
    *,
    retailer: str,
    field_map: dict | None = None,
    price_in_cents: bool = False,
    default_category: str | None = None,
) -> Product | None:
    """One scraped item -> :class:`Product`, or ``None`` when it has no usable name or price."""
    if not isinstance(raw, dict):
        return None
    field_map = field_map or {}
    name = _first(raw, _NAME_KEYS, field_map.get("name"))
    price = parse_price(_first(raw, _PRICE_KEYS, field_map.get("price")), in_cents=price_in_cents)
    if not name or price is None:
        return None
    stock = _first(raw, _STOCK_KEYS, field_map.get("in_stock"))
    image = _first(raw, _IMAGE_KEYS, field_map.get("image_url"))
    category = _first(raw, _CATEGORY_KEYS, field_map.get("category"))
    brand = _first(raw, _BRAND_KEYS, field_map.get("brand"))
    if isinstance(category, (list, tuple)):
        category = category[-1] if category else None
    return Product(
        name=" ".join(str(name).split())[:150],
        barcode=clean_barcode(_first(raw, _BARCODE_KEYS, field_map.get("barcode"))),
        price=price,
        image_url=clean_image_url(image),
        store_name=retailer,
        store_address=None,
        store_lat=None,
        store_lng=None,
        category=str(category) if category else default_category,
        in_stock=True if stock is None else _to_bool(stock),
        brand=str(brand) if isinstance(brand, (str, int)) and brand else None,
        retailer=retailer,
    )


def _fill(template, values: dict):
    """Substitute ``{query}``, ``{lat}`` ... inside an input template (a value that is only a placeholder keeps its type)."""
    if isinstance(template, str):
        whole = re.fullmatch(r"\{(\w+)\}", template)
        if whole and whole.group(1) in values:
            return values[whole.group(1)]
        return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), template)
    if isinstance(template, dict):
        return {key: _fill(value, values) for key, value in template.items()}
    if isinstance(template, list):
        return [_fill(value, values) for value in template]
    return template


def _extract_items(payload) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _CONTAINER_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):  # e.g. {"data": {"products": [...]}}
                nested = _extract_items(value)
                if nested:
                    return nested
    return []


# ---------------------------------------------------------------------------------------------- live base
def _retail_request(service: str, method: str, url: str, **kwargs):
    """``request_with_retry`` that only ever raises :class:`RetailAPIError`, which the pages know how to show."""
    try:
        return request_with_retry(service, method, url, **kwargs)
    except RetailAPIError:
        raise
    except ExternalAPIError as exc:
        raise RetailAPIError(str(exc), service=exc.service or service, status=exc.status) from exc


class _LiveProvider(RetailProvider):
    """Shared behaviour of the scraping providers."""

    def _attach_branches(self, products: list[Product], lat, lng, radius_km) -> list[Product]:
        """Pin each row to the nearest branch of its retailer (from the ``stores`` table) when there is one."""
        from app.services.stores import stores_near

        if lat is None or lng is None:
            default = _default_center()
            lat, lng = default["lat"], default["lng"]
        nearest: dict[str, tuple | None] = {}
        result = []
        for product in products:
            retailer = product.retailer or ""
            if retailer not in nearest:
                found = stores_near(lat, lng, radius_km, brand=retailer, limit=1)
                nearest[retailer] = found[0] if found else None
            branch = nearest[retailer]
            if branch is None:
                result.append(product)
                continue
            store, distance = branch
            result.append(
                replace(
                    product,
                    store_name=store.Name,
                    store_address=store.Address,
                    store_lat=store.Latitude,
                    store_lng=store.Longitude,
                    store_id=store.StoreId,
                    distance_km=round(distance, 2),
                )
            )
        return sorted(result, key=lambda p: (p.name.casefold(), p.price))

    def get_offers_by_barcode(self, barcode, lat=None, lng=None, radius_km=15):
        barcode = clean_barcode(barcode)
        if not barcode:
            return []
        # Neither service has a barcode lookup we can rely on: search for the number and keep exact matches.
        hits = self.search_products(barcode, lat, lng, radius_km)
        return sorted((p for p in hits if p.barcode == barcode), key=lambda p: (p.price, p.store_name))

    def get_price_history(self, barcode, store_id):
        return []  # none of the live sources publish price history


class ApifyProvider(_LiveProvider):
    """Runs an Apify actor per retailer and reads its dataset.

    ``actors`` maps a retailer to ``{"actor": "user~actor-name", "input": {...}, "field_map": {...},
    "price_in_cents": false}``. In ``input`` the placeholders ``{query}``, ``{lat}``, ``{lng}``, ``{radius_km}`` and
    ``{category}`` are replaced for each search. Take the real field names from the actor's Input tab on apify.com.
    """

    name = "apify"
    FINISHED_BAD = {"FAILED", "ABORTED", "ABORTING", "TIMED-OUT", "TIMING-OUT"}

    def __init__(
        self,
        token: str,
        actors: dict,
        *,
        base_url="https://api.apify.com/v2",
        run_timeout=120,
        page_size=100,
        max_items=200,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        if not token:
            raise RetailConfigError("APIFY_API_TOKEN is not set.")
        self.actors = {}
        for key, config in (actors or {}).items():
            retailer = canonical_retailer(key)
            if retailer is None or not isinstance(config, dict) or not config.get("actor"):
                raise RetailConfigError(f"APIFY_ACTORS_JSON: entry {key!r} needs a known retailer name and an 'actor'.")
            self.actors[retailer] = config
        if not self.actors:
            raise RetailConfigError("APIFY_ACTORS_JSON is empty: configure at least one retailer actor.")
        self.token, self.base_url = token, base_url.rstrip("/")
        self.run_timeout, self.page_size, self.max_items = run_timeout, page_size, max_items
        self._sleep, self._clock = sleep, clock

    @classmethod
    def from_config(cls, cfg) -> "ApifyProvider":
        try:
            actors = json.loads(cfg.get("APIFY_ACTORS_JSON") or "{}")
        except ValueError as exc:
            raise RetailConfigError(f"APIFY_ACTORS_JSON is not valid JSON: {exc}") from exc
        return cls(
            cfg.get("APIFY_API_TOKEN"),
            actors,
            base_url=cfg["APIFY_BASE_URL"],
            run_timeout=cfg["APIFY_RUN_TIMEOUT"],
            page_size=cfg["APIFY_PAGE_SIZE"],
        )

    # -- HTTP ------------------------------------------------------------------------------------------------------------
    def _call(self, method, path, **kwargs):
        response = _retail_request(
            "apify",
            method,
            f"{self.base_url}{path}",
            timeout=kwargs.pop("timeout", 70),
            headers={"Authorization": f"Bearer {self.token}"},
            **kwargs,
        )
        if response.status_code in (401, 403):
            raise RetailAPIError("Apify rejected the API token.", service="apify", status=response.status_code)
        if response.status_code >= 400:
            raise RetailAPIError(
                f"Apify answered HTTP {response.status_code}.", service="apify", status=response.status_code
            )
        return response

    def _run_actor(self, actor: str, payload: dict) -> list:
        actor_id = actor.replace("/", "~")
        started = self._call("POST", f"/acts/{actor_id}/runs", json=payload, params={"waitForFinish": 60})
        run = (started.json() or {}).get("data") or {}
        deadline = self._clock() + self.run_timeout
        while run.get("status") in ("READY", "RUNNING"):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise RetailAPIError(f"Apify actor {actor} did not finish within {self.run_timeout}s.", service="apify")
            self._sleep(1)
            run = (
                self._call("GET", f"/actor-runs/{run['id']}", params={"waitForFinish": int(min(30, remaining))}).json()
                or {}
            ).get("data") or {}
        if run.get("status") != "SUCCEEDED":
            raise RetailAPIError(f"Apify actor {actor} ended as {run.get('status') or 'unknown'}.", service="apify")

        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            raise RetailAPIError(f"Apify actor {actor} returned no dataset.", service="apify")
        items: list = []
        while len(items) < self.max_items:
            limit = min(self.page_size, self.max_items - len(items))
            page = self._call(
                "GET",
                f"/datasets/{dataset_id}/items",
                params={"format": "json", "clean": "true", "offset": len(items), "limit": limit},
            ).json()
            if not isinstance(page, list):
                raise RetailAPIError("Apify dataset was not a JSON list.", service="apify")
            items.extend(page)
            if len(page) < limit:
                break  # a short page is the last page
        return items[: self.max_items]

    # -- interface ----------------------------------------------------------------------------------------------------------
    def search_products(self, query, lat, lng, radius_km=15, category=None):
        if not (query or category):
            return []
        values = {"query": query or "", "lat": lat, "lng": lng, "radius_km": radius_km, "category": category or ""}
        products, failures = [], []
        for retailer, config in self.actors.items():
            try:
                items = self._run_actor(config["actor"], _fill(config.get("input") or {"search": "{query}"}, values))
            except RetailAPIError as exc:
                logger.warning("Apify search for %s failed: %s", retailer, exc)
                failures.append(exc)
                continue
            for raw in items:
                product = normalise_product(
                    raw,
                    retailer=retailer,
                    field_map=config.get("field_map"),
                    price_in_cents=bool(config.get("price_in_cents")),
                    default_category=category,
                )
                if product:
                    products.append(product)
        if failures and len(failures) == len(self.actors):
            raise failures[0]  # nothing worked: do not pretend "no results"
        return self._attach_branches(products, lat, lng, radius_km)


class ParseBotProvider(_LiveProvider):
    """Parse.bot hosted scrapers: ``GET {base}/scraper/<id>/search_products?query=&offset=`` with ``X-API-Key``.

    A Woolworths scraper is built in; add or replace scrapers with ``PARSEBOT_SCRAPERS_JSON``
    (``{"woolworths": "<scraper id>"}``). Each retailer's scraper must expose ``search_products``.
    """

    name = "parsebot"
    DEFAULT_SCRAPERS = {"Woolworths": "3c08f360-415f-4984-bcd1-846bc0d7dafc"}

    def __init__(
        self,
        api_key: str,
        scrapers: dict | None = None,
        *,
        base_url="https://api.parse.bot",
        max_pages: int = 5,
        max_items: int = 200,
    ):
        if not api_key:
            raise RetailConfigError("PARSEBOT_API_KEY is not set.")
        self.api_key, self.base_url = api_key, base_url.rstrip("/")
        self.max_pages, self.max_items = max_pages, max_items
        self.scrapers = dict(self.DEFAULT_SCRAPERS)
        for key, scraper_id in (scrapers or {}).items():
            retailer = canonical_retailer(key)
            if retailer is None or not scraper_id:
                raise RetailConfigError(
                    f"PARSEBOT_SCRAPERS_JSON: entry {key!r} needs a known retailer name and a scraper id."
                )
            self.scrapers[retailer] = str(scraper_id)

    @classmethod
    def from_config(cls, cfg) -> "ParseBotProvider":
        try:
            scrapers = json.loads(cfg.get("PARSEBOT_SCRAPERS_JSON") or "{}")
        except ValueError as exc:
            raise RetailConfigError(f"PARSEBOT_SCRAPERS_JSON is not valid JSON: {exc}") from exc
        return cls(cfg.get("PARSEBOT_API_KEY"), scrapers, base_url=cfg["PARSEBOT_BASE_URL"])

    def _search_one(self, retailer: str, scraper_id: str, query: str) -> list:
        items: list = []
        for _page in range(self.max_pages):
            response = _retail_request(
                "parsebot",
                "GET",
                f"{self.base_url}/scraper/{scraper_id}/search_products",
                params={"query": query, "offset": len(items)},
                headers={"X-API-Key": self.api_key},
                timeout=30,
            )
            status = response.status_code
            if status in (401, 403):
                raise RetailAPIError("Parse.bot rejected the API key.", service="parsebot", status=status)
            if status == 402:
                raise RetailAPIError("Parse.bot account is out of credits.", service="parsebot", status=status)
            if status >= 400:
                raise RetailAPIError(f"Parse.bot answered HTTP {status}.", service="parsebot", status=status)
            try:
                payload = response.json()
            except ValueError as exc:
                raise RetailAPIError("Parse.bot returned invalid JSON.", service="parsebot") from exc
            page = _extract_items(payload)
            if not page:
                break
            items.extend(page)
            total = payload.get("total") if isinstance(payload, dict) else None
            if len(items) >= self.max_items or (isinstance(total, int) and len(items) >= total):
                break
        return items[: self.max_items]

    def search_products(self, query, lat, lng, radius_km=15, category=None):
        if not query:
            return []
        products, failures = [], []
        for retailer, scraper_id in self.scrapers.items():
            try:
                items = self._search_one(retailer, scraper_id, query)
            except RetailAPIError as exc:
                logger.warning("Parse.bot search for %s failed: %s", retailer, exc)
                failures.append(exc)
                continue
            for raw in items:
                product = normalise_product(raw, retailer=retailer, default_category=category)
                if product and (not category or (product.category or category).casefold() == category.casefold()):
                    products.append(product)
        if failures and len(failures) == len(self.scrapers):
            raise failures[0]
        return self._attach_branches(products, lat, lng, radius_km)


def _retailer_label(name) -> str | None:
    """``"shoprite"`` -> ``"Shoprite"``, ``"pnp"`` -> ``"Pick n Pay"``, ``"food-lovers-market"`` -> ``"Food Lovers Market"``."""
    if isinstance(name, dict):
        name = _first(name, ("name", "title", "label"))
    if not isinstance(name, str) or not name.strip():
        return None
    known = canonical_retailer(name)
    if known:
        return known
    words = re.sub(r"[\s_\-]+", " ", name.strip())
    return words.title() if words == words.lower() or words == words.upper() else words


_RETAILER_KEYS = (
    "retailer",
    "retailerName",
    "retailer_name",
    "store",
    "storeName",
    "store_name",
    "merchant",
    "shop",
    "seller",
)
_OFFER_LIST_KEYS = ("prices", "offers", "retailers", "stores", "listings")


class LoyaltyHubProvider(_LiveProvider):
    """LoyaltyHub's SA Grocery Price API (https://loyaltyhub.co.za/developers).

    ``GET {base}/prices?search=<text>&limit=<n>`` and ``GET {base}/prices?barcode=<gtin>`` with
    ``Authorization: Bearer <key>``. Every row of ``data`` is one retailer's price for one product:
    ``{"retailer": "shoprite", "barcode": "6001030002075", "name": "...", "price": 21.99, "in_stock": true,
    "image_url": "https://..."}``, and ``meta.quota`` / ``meta.used`` say how much of the month's calls are left.

    The free key allows 100 calls a month and 20 a minute, so a search is a single call (no paging) and answers are
    cached for ``cache_ttl`` (a day). Prices are per retailer, not per branch: each row is pinned to the nearest branch
    in our ``stores`` table so distance and routing still work. Product pictures come straight from ``image_url``.
    """

    name = "loyaltyhub"
    SERVICE = "loyaltyhub"

    def __init__(self, api_key: str, *, base_url="https://loyaltyhub.co.za/api/v1", page_size=100, cache_ttl=900):
        if not api_key:
            raise RetailConfigError("LOYALTYHUB_API_KEY is not set.")
        self.api_key, self.base_url = api_key, base_url.rstrip("/")
        self.page_size = max(1, min(int(page_size), 500))  # the API's own maximum is 500
        self.cache_ttl = int(cache_ttl)
        self.quota: dict = {}  # the last meta.quota / meta.used seen, for the admin page and the logs

    @classmethod
    def from_config(cls, cfg) -> "LoyaltyHubProvider":
        return cls(
            cfg.get("LOYALTYHUB_API_KEY"),
            base_url=cfg["LOYALTYHUB_BASE_URL"],
            page_size=cfg["LOYALTYHUB_PAGE_SIZE"],
            cache_ttl=cfg["LOYALTYHUB_CACHE_TTL"],
        )

    def _get(self, path: str, params: dict) -> list:
        try:
            response = _retail_request(
                self.SERVICE,
                "GET",
                f"{self.base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
                timeout=20,
                retries=1,  # a monthly quota does not come back after a retry, so do not burn calls on it
            )
        except RetailAPIError as exc:
            if exc.status == 429:
                raise RetailAPIError(
                    "LoyaltyHub's call limit is used up for now (the free key allows 100 a month and 20 a minute). "
                    "Try again later or upgrade the LoyaltyHub plan.",
                    service=self.SERVICE,
                    status=429,
                ) from exc
            raise
        status = response.status_code
        if status in (401, 403):
            raise RetailAPIError("LoyaltyHub rejected the API key.", service=self.SERVICE, status=status)
        if status == 404:
            return []
        if status >= 400:
            raise RetailAPIError(f"LoyaltyHub answered HTTP {status}.", service=self.SERVICE, status=status)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RetailAPIError("LoyaltyHub returned invalid JSON.", service=self.SERVICE) from exc
        meta = payload.get("meta") if isinstance(payload, dict) else None
        if isinstance(meta, dict) and meta.get("quota") is not None:
            self.quota = {"quota": meta.get("quota"), "used": meta.get("used")}
        return _extract_items(payload)

    def _products(self, rows: list, category: str | None) -> list[Product]:
        products = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            retailer = _retailer_label(_first(raw, _RETAILER_KEYS)) or "LoyaltyHub"
            product = normalise_product(raw, retailer=retailer, default_category=category or "Grocery")
            if product is not None:
                products.append(product)
        return products

    def search_products(self, query, lat, lng, radius_km=15, category=None):
        query = (query or "").strip()
        if not (query or category):
            return []
        params: dict = {"limit": self.page_size}
        if query:
            params["search"] = query
        else:
            params["category"] = category
        products = self._products(self._get("/prices", params), category)
        if category:
            products = [p for p in products if (p.category or category).casefold() == category.casefold()]
        return self._attach_branches(products, lat, lng, radius_km)

    def get_offers_by_barcode(self, barcode, lat=None, lng=None, radius_km=15):
        barcode = clean_barcode(barcode)
        if not barcode:
            return []
        rows = self._get("/prices", {"barcode": barcode, "limit": self.page_size})
        offers = [p for p in self._products(rows, None) if p.barcode == barcode]
        return sorted(self._attach_branches(offers, lat, lng, radius_km), key=lambda p: (p.price, p.store_name))


class BuylyProvider(_LiveProvider):
    """Buyly's product API (https://buyly.co.za, closed beta: request a key from Buyly).

    Endpoints from Buyly's public page: ``GET {base}/products/search?q=<text>`` and ``GET {base}/products/<barcode>``
    with ``Authorization: Bearer <key>``. Buyly does not publish the response format, so nothing is assumed: the
    answer may be a list or an envelope (``data``/``products``/``items``/``results``), each item may be one offer or
    hold a list of retailer offers (``prices``/``offers``/``retailers``/``stores``), and field names are recognised
    the way they are for the scrapers. **This connector has not been run against a real Buyly key.** If your key's
    responses look different, "Sync now" in Admin > Integrations will say what went wrong and the mapping in
    :func:`_buyly_rows` is the only place to adjust.
    """

    name = "buyly"
    SERVICE = "buyly"

    def __init__(self, api_key: str, *, base_url="https://api.buyly.co.za/v1", cache_ttl=3600, limit=50):
        if not api_key:
            raise RetailConfigError("BUYLY_API_KEY is not set.")
        self.api_key, self.base_url = api_key, base_url.rstrip("/")
        self.cache_ttl, self.limit = int(cache_ttl), limit

    @classmethod
    def from_config(cls, cfg) -> "BuylyProvider":
        return cls(cfg.get("BUYLY_API_KEY"), base_url=cfg["BUYLY_BASE_URL"], cache_ttl=cfg["BUYLY_CACHE_TTL"])

    def _get(self, path: str, params: dict | None = None):
        try:
            response = _retail_request(
                self.SERVICE,
                "GET",
                f"{self.base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
                timeout=20,
            )
        except RetailAPIError as exc:
            if exc.status == 429:
                raise RetailAPIError(
                    "Buyly's request limit was reached. Try again in a while.", service=self.SERVICE, status=429
                ) from exc
            raise
        status = response.status_code
        if status in (401, 403):
            raise RetailAPIError(
                "Buyly rejected the API key (is the beta key approved and active?).",
                service=self.SERVICE,
                status=status,
            )
        if status == 404:
            return None
        if status >= 400:
            raise RetailAPIError(f"Buyly answered HTTP {status}.", service=self.SERVICE, status=status)
        try:
            return response.json()
        except ValueError as exc:
            raise RetailAPIError("Buyly returned invalid JSON.", service=self.SERVICE) from exc

    def _products(self, payload, category: str | None) -> list[Product]:
        products = []
        for raw in _buyly_rows(payload):
            retailer = _retailer_label(_first(raw, _RETAILER_KEYS)) or "Buyly"
            product = normalise_product(raw, retailer=retailer, default_category=category or "Grocery")
            if product is not None:
                products.append(product)
        return products

    def search_products(self, query, lat, lng, radius_km=15, category=None):
        query = (query or "").strip()
        if not query:
            return []
        products = self._products(self._get("/products/search", {"q": query, "limit": self.limit}), category)
        if category:
            products = [p for p in products if (p.category or category).casefold() == category.casefold()]
        return self._attach_branches(products, lat, lng, radius_km)

    def get_offers_by_barcode(self, barcode, lat=None, lng=None, radius_km=15):
        barcode = clean_barcode(barcode)
        if not barcode:
            return []
        payload = self._get(f"/products/{barcode}")
        offers = []
        for product in self._products(payload, None):
            # A single-product answer may omit the barcode; we asked for this one, so it is this one.
            offers.append(product if product.barcode else replace(product, barcode=barcode))
        offers = [p for p in offers if p.barcode == barcode]
        return sorted(self._attach_branches(offers, lat, lng, radius_km), key=lambda p: (p.price, p.store_name))


def _buyly_rows(payload) -> list[dict]:
    """Flatten whatever Buyly sends into one dict per retailer offer (the product's own fields are copied onto each)."""
    items = _extract_items(payload)
    if not items and isinstance(payload, dict):  # ``GET /products/<barcode>`` may answer with the bare product
        for holder in (payload, *(payload.get(key) for key in ("data", "product", "result"))):
            if isinstance(holder, dict) and _first(holder, _NAME_KEYS):
                items = [holder]
                break
    rows: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in _OFFER_LIST_KEYS:
            offers = item.get(key)
            if isinstance(offers, list) and offers and all(isinstance(offer, dict) for offer in offers):
                base = {k: v for k, v in item.items() if k != key}
                rows.extend({**base, **offer} for offer in offers)
                break
        else:
            rows.append(item)
    return rows


# ---------------------------------------------------------------------------------------------- caching + factory
def _round(value) -> float | None:
    return None if value is None else round(float(value), 2)  # ~1 km: nearby students share cache entries


RETAIL_CACHE_VERSION_KEY = "retail:version"
MAX_RAW_CATEGORIES_PER_BROWSE = 5  # extra provider calls when browsing a category that admins mapped raw names onto


def invalidate_retail_cache() -> None:
    """Make every cached retail answer stale at once (the admin portal's "Sync now"): keys include this version."""
    cache.set(RETAIL_CACHE_VERSION_KEY, time.time_ns(), timeout=0)


class CachedProvider(RetailProvider):
    """Wraps a provider with Flask-Caching (``RETAIL_CACHE_TTL``, 15 minutes). Failures are never cached.

    ``categories`` (optional) returns the admin's category mappings ``{key: (raw name, mapped category)}``; they are
    applied to every answer *after* the cache, so editing a mapping needs no cache clearing.
    """

    def __init__(self, inner: RetailProvider, ttl: int, categories=None):
        self.inner, self.ttl, self.name, self.categories = inner, ttl, inner.name, categories

    def _cached(self, method: str, args: tuple, compute):
        raw = json.dumps([self.inner.name, cache.get(RETAIL_CACHE_VERSION_KEY) or 0, method, *args], default=str)
        key = "retail:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()
        hit = cache.get(key)
        if hit is not None:
            return hit["value"]
        value = compute()
        cache.set(key, {"value": value}, timeout=self.ttl)
        return value

    # -- category mappings ---------------------------------------------------------------------------------------------
    def _table(self) -> dict:
        return (self.categories() if self.categories else None) or {}

    @staticmethod
    def _apply(products: list[Product], table: dict) -> list[Product]:
        if not table:
            return products
        out = []
        for product in products:
            mapped = table.get(_category_key(product.category))
            out.append(replace(product, category=mapped[1]) if mapped and mapped[1] != product.category else product)
        return out

    def _search(self, query, lat, lng, radius_km, category):
        args = ((query or "").strip().casefold(), _round(lat), _round(lng), radius_km, (category or "").casefold())
        return self._cached("search", args, lambda: self.inner.search_products(query, lat, lng, radius_km, category))

    def search_products(self, query, lat, lng, radius_km=15, category=None):
        table = self._table()
        wanted = (category or "").strip().casefold()
        if not (wanted and any(mapped.casefold() == wanted for _, mapped in table.values())):
            return self._apply(self._search(query, lat, lng, radius_km, category), table)
        # Some raw category names were mapped onto the category being asked for, so the provider cannot filter for us.
        if (query or "").strip():
            found = self._search(query, lat, lng, radius_km, None)  # everything, then filter after mapping
        else:  # browsing: the category itself + each raw name
            raws = [raw for raw, mapped in table.values() if mapped.casefold() == wanted][
                :MAX_RAW_CATEGORIES_PER_BROWSE
            ]
            found, seen = [], set()
            for name in [category, *raws]:
                for product in self._search(query, lat, lng, radius_km, name):
                    identity = (product.barcode, product.store_name, product.name)
                    if identity not in seen:
                        seen.add(identity)
                        found.append(product)
        return [p for p in self._apply(found, table) if (p.category or "").casefold() == wanted]

    def get_offers_by_barcode(self, barcode, lat=None, lng=None, radius_km=15):
        offers = self._cached(
            "offers",
            (barcode, _round(lat), _round(lng), radius_km),
            lambda: self.inner.get_offers_by_barcode(barcode, lat, lng, radius_km),
        )
        return self._apply(offers, self._table())

    def get_stores_near(self, lat, lng, radius_km=15):
        return self._cached(
            "stores", (_round(lat), _round(lng), radius_km), lambda: self.inner.get_stores_near(lat, lng, radius_km)
        )

    def get_price_history(self, barcode, store_id):
        return self._cached("history", (barcode, store_id), lambda: self.inner.get_price_history(barcode, store_id))


def _category_key(category: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", category or "")).strip().casefold()


PROVIDERS = {
    "mock": lambda cfg: MockProvider(),
    "loyaltyhub": LoyaltyHubProvider.from_config,
    "buyly": BuylyProvider.from_config,
    "apify": ApifyProvider.from_config,
    "parsebot": ParseBotProvider.from_config,
}


def build_provider(cfg) -> RetailProvider:
    """The (uncached) provider named by ``RETAIL_PROVIDER``. Raises :class:`RetailConfigError` if it cannot be built."""
    name = (cfg.get("RETAIL_PROVIDER") or "mock").strip().casefold()
    try:
        return PROVIDERS[name](cfg)
    except KeyError:
        raise RetailConfigError(f"Unknown RETAIL_PROVIDER {name!r}; choose one of {sorted(PROVIDERS)}.") from None


def get_retail_provider() -> RetailProvider:
    """The app's cached retail provider.

    Which source it talks to, and with which key, comes from the admin portal's integration settings when an admin
    has chosen one, otherwise from the environment (``RETAIL_PROVIDER`` and the API keys). The settings are read again
    every ``RETAIL_SETTINGS_RECHECK_SECONDS`` (so every gunicorn worker notices a change), and the provider is only
    rebuilt when they actually differ.
    """
    from app.services import category_map, integrations

    app = current_app._get_current_object()
    entry = app.extensions.get("retail_provider")
    now = time.monotonic()
    if entry is not None and now - entry[1] < app.config.get("RETAIL_SETTINGS_RECHECK_SECONDS", 30):
        return entry[0]
    cfg = integrations.effective_config(app.config)
    signature = tuple(
        cfg.get(key)
        for key in ("RETAIL_PROVIDER", "APIFY_API_TOKEN", "PARSEBOT_API_KEY", "LOYALTYHUB_API_KEY", "BUYLY_API_KEY")
    )
    if entry is not None and entry[2] == signature:
        app.extensions["retail_provider"] = (entry[0], now, signature)
        return entry[0]
    inner = build_provider(cfg)
    ttl = getattr(inner, "cache_ttl", None) or app.config["RETAIL_CACHE_TTL"]
    provider = CachedProvider(inner, ttl, categories=category_map.table)
    app.extensions["retail_provider"] = (provider, now, signature)
    return provider


def reset_retail_provider() -> None:
    """Forget the built provider so the next call rebuilds it from the current settings (after an admin change)."""
    if has_app_context():
        current_app.extensions.pop("retail_provider", None)
