"""Admin > Built-in products: edit, delete and restore the products of the built-in (mock) catalogue.

The catalogue itself is code (``app/data/mock_products.py``); every change is stored as an override
(``MockProductOverride`` / ``MockRetailerOverride``) and applied by ``MockProvider``. Callers commit, then call
:func:`app.services.retail_api.invalidate_retail_cache` so students see the change at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.data.mock_products import CATALOGUE
from app.extensions import db
from app.models import MockProductOverride, MockRetailerOverride
from app.services.retail_api import _BY_BARCODE, CatalogueEdits, MockProvider, edited_catalogue

CHAINS = ("Checkers", "Pick n Pay", "Shoprite", "Woolworths", "SPAR")  # the chains the built-in prices cover
CATEGORIES = ("Grocery", "Toiletries", "Clothes")
PER_PAGE = 25


class BuiltinError(ValueError):
    """A change that is refused; the message is safe to show."""


@dataclass
class ChainRow:
    retailer: str
    listed: bool
    price: Decimal
    in_stock: bool
    auto_listed: bool
    auto_price: Decimal
    auto_in_stock: bool

    @property
    def changed(self) -> bool:
        return (self.listed, self.price, self.in_stock) != (self.auto_listed, self.auto_price, self.auto_in_stock)


@dataclass
class BuiltinProduct:
    original: object  # CatalogueItem as shipped
    item: object  # CatalogueItem as edited
    hidden: bool
    edited: bool
    chains: list[ChainRow]


def search(query: str = "", category: str | None = None, status: str | None = None, page: int = 1):
    """``(rows, total, page, pages)`` of ``(edited item, hidden?, edited?)``, filtered."""
    edits = CatalogueEdits.load()
    words = (query or "").casefold().split()
    rows = []
    for item, hidden in edited_catalogue():
        changed = item.barcode in edits.items or any(k[0] == item.barcode for k in edits.retail)
        if category and item.category != category:
            continue
        if status == "deleted" and not hidden or status == "edited" and not changed or status == "active" and hidden:
            continue
        if words and not all(w in f"{item.name} {item.brand} {item.barcode} {item.category}".casefold() for w in words):
            continue
        rows.append((item, hidden, changed))
    pages = max(1, -(-len(rows) // PER_PAGE))
    page = min(max(1, page), pages)
    return rows[(page - 1) * PER_PAGE : page * PER_PAGE], len(rows), page, pages


def counts() -> dict:
    edits = CatalogueEdits.load()
    return {
        "total": len(CATALOGUE),
        "deleted": sum(1 for row in edits.items.values() if row.Hidden),
        "edited": len({b for b in edits.items} | {b for b, _ in edits.retail}),
    }


def get(barcode: str) -> BuiltinProduct | None:
    original = _BY_BARCODE.get(barcode)
    if original is None:
        return None
    edits = CatalogueEdits.load()
    item = edits.item(original)
    provider, empty = MockProvider(), CatalogueEdits({}, {})
    chains = []
    for retailer in CHAINS:
        listed, price, in_stock = provider.chain_offer(item, retailer, edits)
        auto = provider.chain_offer(item, retailer, empty)
        chains.append(ChainRow(retailer, listed, price, in_stock, *auto))
    edited = barcode in edits.items or any(k[0] == barcode for k in edits.retail)
    return BuiltinProduct(original, item, edits.hidden(barcode), edited, chains)


def _override(barcode: str) -> MockProductOverride:
    row = db.session.get(MockProductOverride, barcode)
    if row is None:
        row = MockProductOverride(Barcode=barcode, Hidden=False)
        db.session.add(row)
    return row


def save(barcode: str, form, admin_email: str) -> dict:
    """Apply the edit form. Returns what changed (for the audit log). Raises :class:`BuiltinError`."""
    from app.utils.money import parse_amount

    product = get(barcode)
    if product is None:
        raise BuiltinError("That product is not in the built-in catalogue.")
    original = product.original

    name = " ".join((form.get("name") or "").split())
    brand = " ".join((form.get("brand") or "").split())
    category = form.get("category") or original.category
    if not 2 <= len(name) <= 150:
        raise BuiltinError("The name must be between 2 and 150 characters.")
    if len(brand) > 80:
        raise BuiltinError("The brand can be at most 80 characters.")
    if category not in CATEGORIES:
        raise BuiltinError("Choose Grocery, Toiletries or Clothes.")
    price = parse_amount(form.get("price") or "")
    if price is None or price <= 0:
        raise BuiltinError("Enter the reference price, for example 24.99.")

    row = _override(barcode)
    row.Name = name if name != original.name else None
    row.Brand = brand if brand and brand != original.brand else None
    row.Category = category if category != original.category else None
    row.Price = price if price != Decimal(original.price) else None
    row.UpdatedBy = admin_email

    # Per chain: stocked?, price (blank = worked out from the reference price), in stock?
    edited = CatalogueEdits.load()  # the reference price may just have changed: compare with the automatic values
    edited.items[barcode] = row
    item = edited.item(original)
    provider, empty = MockProvider(), CatalogueEdits({}, {})
    for retailer in CHAINS:
        auto_listed, auto_price, auto_stock = provider.chain_offer(item, retailer, empty)
        key = retailer.replace(" ", "_")
        listed = form.get(f"listed_{key}") == "1"
        in_stock = form.get(f"stock_{key}") == "1"
        raw = (form.get(f"price_{key}") or "").strip()
        chain_price = parse_amount(raw) if raw else None
        if raw and (chain_price is None or chain_price <= 0):
            raise BuiltinError(f"Enter a price for {retailer} in rand, or leave it empty for the automatic price.")
        chain = (
            db.session.query(MockRetailerOverride)
            .filter(MockRetailerOverride.Barcode == barcode, MockRetailerOverride.Retailer == retailer)
            .one_or_none()
        )
        values = {
            "Listed": None if listed == auto_listed else listed,
            "Price": None if chain_price is None or chain_price == auto_price else chain_price,
            "InStock": None if in_stock == auto_stock else in_stock,
        }
        if all(v is None for v in values.values()):
            if chain is not None:
                db.session.delete(chain)
            continue
        if chain is None:
            chain = MockRetailerOverride(Barcode=barcode, Retailer=retailer)
            db.session.add(chain)
        chain.Listed, chain.Price, chain.InStock = values["Listed"], values["Price"], values["InStock"]
    return {"name": name, "brand": brand, "category": category, "price": str(price)}


def set_hidden(barcode: str, hidden: bool, admin_email: str) -> None:
    if barcode not in _BY_BARCODE:
        raise BuiltinError("That product is not in the built-in catalogue.")
    row = _override(barcode)
    row.Hidden = hidden
    row.UpdatedBy = admin_email


def reset(barcode: str) -> None:
    """Forget every edit of this product (it goes back to the built-in values and is shown again)."""
    db.session.query(MockRetailerOverride).filter(MockRetailerOverride.Barcode == barcode).delete()
    row = db.session.get(MockProductOverride, barcode)
    if row is not None:
        db.session.delete(row)
