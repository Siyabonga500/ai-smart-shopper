"""The shopping list (Steps 11 and 12): add products, change quantities, cheaper alternatives, replace.

Prices always come from the retail provider on the server, never from the browser: the browser only says which
product (barcode + store) the student pressed "Add" on, and the server looks the current price up again. So a
tampered request cannot put a R1 price on a R100 item.

One item per product per store: adding the same barcode at the same store again raises the quantity instead of
making a duplicate row (products without a barcode are matched on name + store).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from flask import current_app

from app.extensions import db
from app.models import Budget, ListItem, ShoppingList, User
from app.services import budgets
from app.services.products import similar_offers
from app.services.retail_api import Product, RetailAPIError, RetailProvider
from app.utils.dates import as_utc, utcnow
from app.utils.formatters import format_zar
from app.utils.geo import distance_km, map_center_for
from app.utils.money import ZERO, money, to_decimal


class ShoppingError(ValueError):
    """Something the student asked for cannot be done. ``status`` is the HTTP status the API should answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AddResult:
    item: ListItem
    created: bool
    shopping_list: ShoppingList
    position: budgets.BudgetPosition

    @property
    def alert(self) -> str | None:
        return over_budget_message(self.position)


def over_budget_message(pos: budgets.BudgetPosition) -> str | None:
    """ "Your shopping list has exceeded your allocated budget by R100." - or ``None`` while within budget."""
    if not pos.is_over:
        return None
    return f"Your shopping list has exceeded your allocated budget by {format_zar(pos.over_by)}."


def student_center(user: User) -> dict:
    """Where the student searches from: their saved address, else Durban."""
    return map_center_for(user.Latitude, user.Longitude)


# ---------------------------------------------------------------------------------------------------- identity
def _same_product(item: ListItem, barcode: str | None, store_name: str | None, name: str | None) -> bool:
    if (item.StoreName or "") != (store_name or ""):
        return False
    if barcode or item.BarCode:
        return (item.BarCode or "") == (barcode or "")
    return (item.ItemName or "").casefold() == (name or "").casefold()


def find_item(items: list[ListItem], barcode: str | None, store_name: str | None, name: str | None) -> ListItem | None:
    return next((item for item in items if _same_product(item, barcode, store_name, name)), None)


# ---------------------------------------------------------------------------------------------------- resolving a product
def resolve_offer(
    provider: RetailProvider,
    user: User,
    *,
    barcode: str | None,
    store_id: str | None = None,
    store_name: str | None = None,
    name: str | None = None,
) -> Product | None:
    """Look the product up again on the server: the offer for ``barcode`` at that store (or by name, when the
    product has no barcode). ``None`` when the store no longer lists it."""
    center = student_center(user)
    radius = current_app.config["SEARCH_MAX_RADIUS_KM"]

    def at_store(product: Product) -> bool:
        if store_id and product.store_id == store_id:
            return True
        return bool(store_name) and product.store_name == store_name

    if barcode:
        offers = provider.get_offers_by_barcode(barcode, center["lat"], center["lng"], radius)
        return next((offer for offer in offers if at_store(offer)), None)
    if name:
        matches = provider.search_products(name, center["lat"], center["lng"], radius)
        return next((offer for offer in matches if at_store(offer) and offer.name.casefold() == name.casefold()), None)
    return None


# ---------------------------------------------------------------------------------------------------- add / change / remove
def _fill_item_from_offer(item: ListItem, offer: Product, budget) -> None:
    item.ItemName = offer.name[:150]
    item.UnitCost = money(offer.price)
    item.StoreName = (offer.store_name or "")[:100] or None
    item.ItemImage = offer.image_url or None
    item.BarCode = (offer.barcode or "")[:50] or None
    item.StoreAddress = offer.store_address
    item.Category = (offer.category or "")[:50] or None
    item.StoreLatitude = offer.store_lat
    item.StoreLongitude = offer.store_lng
    sub_budget = budgets.sub_budget_for(budget, offer.category)
    item.SubBudgetId = sub_budget.SubBudgetId if sub_budget else None


def _active_context(user: User):
    budget = budgets.get_active_budget(user.UserId)
    if budget is None:
        raise ShoppingError("Create a budget first. Your shopping list belongs to your active budget.", 409)
    return budget, budgets.ensure_active_list(budget)


def _owned_active_item(user: User, item_id: str) -> tuple[ListItem, ShoppingList, Budget]:
    budget, shopping_list = _active_context(user)
    item = db.session.get(ListItem, item_id)
    if item is None or item.ShoppingListId != shopping_list.ShoppingListId or item.UserId != user.UserId:
        raise ShoppingError("That item is not on your shopping list.", 404)
    return item, shopping_list, budget


def add_product(user: User, offer: Product, quantity: int = 1) -> AddResult:
    """Add ``offer`` to the active list (or raise its quantity if it is already there).

    Going over budget never blocks an add; the result carries the "exceeded by R100" alert instead.
    """
    budget, shopping_list = _active_context(user)
    max_qty = current_app.config["MAX_ITEM_QUANTITY"]
    items = budgets.list_items(shopping_list)
    existing = find_item(items, offer.barcode, offer.store_name, offer.name)
    try:
        if existing is not None:
            if existing.ItemQuantity + quantity > max_qty:
                raise ShoppingError(f"You can add at most {max_qty} of one item.", 409)
            existing.ItemQuantity += quantity
            existing.UnitCost = money(offer.price)  # the offer was just looked up: every unit is at today's price
            item, created = existing, False
        else:
            item = ListItem(
                UserId=user.UserId,
                ShoppingListId=shopping_list.ShoppingListId,
                ItemQuantity=quantity,
                DateCreated=utcnow(),
            )
            _fill_item_from_offer(item, offer, budget)
            db.session.add(item)
            created = True
        pos = budgets.recalculate(budget)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return AddResult(item, created, shopping_list, pos)


def add_offers(user: User, entries: list[tuple[Product, int]], replace: bool = False) -> budgets.BudgetPosition:
    """Put several products on the active list in one transaction (Reorder from History).

    ``replace`` first clears the list. A product already on the list (same barcode, same store) gains quantity
    instead of a second row. Nothing is saved unless every entry can be, so a failure never leaves half a list.
    """
    budget, shopping_list = _active_context(user)
    max_qty = current_app.config["MAX_ITEM_QUANTITY"]
    try:
        items = budgets.list_items(shopping_list)
        if replace:
            for item in items:
                db.session.delete(item)
            db.session.flush()
            items = []
        for offer, quantity in entries:
            existing = find_item(items, offer.barcode, offer.store_name, offer.name)
            if existing is not None:
                if existing.ItemQuantity + quantity > max_qty:
                    raise ShoppingError(f"That would put more than {max_qty} of {existing.ItemName} on your list.", 409)
                existing.ItemQuantity += quantity
                existing.UnitCost = money(offer.price)  # re-priced: the offer is today's price
                continue
            item = ListItem(
                UserId=user.UserId,
                ShoppingListId=shopping_list.ShoppingListId,
                ItemQuantity=min(quantity, max_qty),
                DateCreated=utcnow(),
            )
            _fill_item_from_offer(item, offer, budget)
            db.session.add(item)
            items.append(item)
        pos = budgets.recalculate(budget)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return pos


def set_quantity(user: User, item_id: str, quantity: int) -> tuple[ListItem, budgets.BudgetPosition]:
    max_qty = current_app.config["MAX_ITEM_QUANTITY"]
    if not 1 <= quantity <= max_qty:
        raise ShoppingError(f"Quantity must be between 1 and {max_qty}. Use the bin to remove an item.")
    item, _, budget = _owned_active_item(user, item_id)
    try:
        item.ItemQuantity = quantity
        pos = budgets.recalculate(budget)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return item, pos


def set_collected(user: User, item_id: str, collected: bool) -> ListItem:
    """Tick an item off as purchased in the shop (or untick it). Only tracking: "Done" still closes the whole list."""
    item, _, _ = _owned_active_item(user, item_id)
    try:
        item.IsCollected = bool(collected)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return item


def remove_item(user: User, item_id: str) -> budgets.BudgetPosition:
    item, _, budget = _owned_active_item(user, item_id)
    try:
        db.session.delete(item)
        pos = budgets.recalculate(budget)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return pos


def rename_list(user: User, title: str) -> ShoppingList:
    title = " ".join((title or "").split())
    if not title:
        raise ShoppingError("The list needs a title.")
    if len(title) > 100:
        raise ShoppingError("Keep the title to 100 characters or fewer.")
    _, shopping_list = _active_context(user)
    shopping_list.Title = title
    db.session.commit()
    return shopping_list


# ---------------------------------------------------------------------------------------------------- cheaper alternatives
MIN_SAVING = Decimal("1.00")  # ignore "alternatives" that save less than a rand


def _alt_dict(offer: Product, unit_cost: Decimal, same_product: bool) -> dict:
    return {
        "name": offer.name,
        "barcode": offer.barcode,
        "price": str(money(offer.price)),
        "saving": str(money(unit_cost) - money(offer.price)),
        "store_name": offer.store_name,
        "store_address": offer.store_address,
        "store_lat": offer.store_lat,
        "store_lng": offer.store_lng,
        "image_url": offer.image_url,
        "category": offer.category,
        "distance_km": offer.distance_km,
        "same_product": same_product,
    }


def find_alternative(provider: RetailProvider, item: ListItem, lat: float, lng: float) -> dict | None:
    """The cheapest way to buy this item for less, or ``None``.

    1. The same product (same barcode) at another store, if it is in stock and cheaper.
    2. Otherwise a similar, cheaper product: same category and pack size, with the same key words once the
       brand (the first word of the name) is left out. This is a heuristic for names that start with the brand.
    """
    radius = current_app.config["SEARCH_MAX_RADIUS_KM"]
    unit_cost = to_decimal(item.UnitCost)
    if item.BarCode:
        offers = [
            o
            for o in provider.get_offers_by_barcode(item.BarCode, lat, lng, radius)
            if o.in_stock and unit_cost - money(o.price) >= MIN_SAVING
        ]
        if offers:
            best = min(
                offers, key=lambda o: (o.price, o.distance_km if o.distance_km is not None else 999, o.store_name)
            )
            return _alt_dict(best, unit_cost, same_product=True)

    similar = [
        o
        for o in similar_offers(provider, item.ItemName, item.Category, lat, lng, radius, item.BarCode)
        if unit_cost - money(o.price) >= MIN_SAVING
    ]
    if not similar:
        return None
    return _alt_dict(similar[0], unit_cost, same_product=False)  # similar_offers sorts cheapest first


def _fresh(info: dict | None, ttl: int, now: datetime) -> bool:
    if not info or "checked_at" not in info:
        return False
    try:
        checked = as_utc(datetime.fromisoformat(info["checked_at"]))
    except ValueError:
        return False
    return (now - checked).total_seconds() < ttl


def refresh_alternatives(items: list[ListItem], provider: RetailProvider, user: User, force: bool = False) -> None:
    """Make sure every item's ``CheaperAlternativeJSON`` is current (checked within ``ALTERNATIVES_TTL``).

    A provider failure leaves the old value alone. Commits when anything changed.
    """
    ttl = current_app.config["ALTERNATIVES_TTL"]
    now = utcnow()
    center = student_center(user)
    changed = False
    for item in items:
        info = item.CheaperAlternativeJSON
        if not force and _fresh(info, ttl, now):
            continue
        try:
            alternative = find_alternative(provider, item, center["lat"], center["lng"])
        except RetailAPIError:
            current_app.logger.warning("Could not check cheaper alternatives for %s", item.ItemName, exc_info=True)
            continue
        item.CheaperAlternativeJSON = {
            "alternative": alternative,
            "replaced": (info or {}).get("replaced"),
            "checked_at": now.isoformat(),
        }
        changed = True
    if changed:
        db.session.commit()


def alternative_of(item: ListItem) -> dict | None:
    info = item.CheaperAlternativeJSON or {}
    return info.get("alternative")


def item_saving(item: ListItem) -> Decimal:
    """What the student saves on this row by taking the alternative: (unit cost - alternative price) x quantity."""
    alt = alternative_of(item)
    if not alt:
        return ZERO
    per_unit = to_decimal(item.UnitCost) - to_decimal(alt.get("price"))
    return max(ZERO, per_unit) * int(item.ItemQuantity or 0)


def potential_savings(items: list[ListItem]) -> Decimal:
    """Dashboard "Potential Savings": the sum of (current price - cheapest alternative) x quantity across the list."""
    return sum((item_saving(item) for item in items), ZERO).quantize(Decimal("0.01"))


def replace_with_alternative(
    provider: RetailProvider, user: User, item_id: str
) -> tuple[ListItem, budgets.BudgetPosition]:
    """The Replace button: swap the item for its cheaper alternative and remember what it used to be.

    The alternative is looked up again first, so a price that changed since the page loaded is honoured.
    Swapped fields: UnitCost, StoreName, StoreAddress, BarCode, ItemImage (and the name, category and
    store position, which belong to the product). The old product goes into ``CheaperAlternativeJSON["replaced"]``.
    If the list already has the alternative product at that store, the two rows are merged.
    """
    item, shopping_list, budget = _owned_active_item(user, item_id)
    center = student_center(user)
    try:
        alt = find_alternative(provider, item, center["lat"], center["lng"])
    except RetailAPIError as exc:
        raise ShoppingError("We could not check prices right now. Please try again.", 502) from exc
    if alt is None:
        raise ShoppingError("There is no cheaper alternative for this item any more.", 409)

    replaced = {
        "name": item.ItemName,
        "barcode": item.BarCode,
        "price": str(money(item.UnitCost)),
        "store_name": item.StoreName,
        "store_address": item.StoreAddress,
        "image_url": item.ItemImage,
        "category": item.Category,
        "store_lat": item.StoreLatitude,
        "store_lng": item.StoreLongitude,
        "replaced_on": utcnow().isoformat(),
    }
    offer = Product(
        name=alt["name"],
        barcode=alt["barcode"],
        price=to_decimal(alt["price"]),
        image_url=alt["image_url"],
        store_name=alt["store_name"],
        store_address=alt["store_address"],
        store_lat=alt["store_lat"],
        store_lng=alt["store_lng"],
        category=alt["category"],
    )
    try:
        others = [i for i in budgets.list_items(shopping_list) if i.ListItemId != item.ListItemId]
        twin = find_item(others, offer.barcode, offer.store_name, offer.name)
        if twin is not None:  # already on the list at that store: merge, no duplicate
            if twin.ItemQuantity + item.ItemQuantity > current_app.config["MAX_ITEM_QUANTITY"]:
                raise ShoppingError(
                    "Merging would put more than the most you can have of one item on your list. "
                    "Lower a quantity first.",
                    409,
                )
            twin.ItemQuantity += item.ItemQuantity
            twin.UnitCost = money(offer.price)
            twin.CheaperAlternativeJSON = {
                "alternative": None,
                "replaced": replaced,
                "checked_at": utcnow().isoformat(),
            }
            db.session.delete(item)
            result = twin
        else:
            _fill_item_from_offer(item, offer, budget)
            item.CheaperAlternativeJSON = {
                "alternative": None,
                "replaced": replaced,
                "checked_at": utcnow().isoformat(),
            }
            result = item
        pos = budgets.recalculate(budget)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return result, pos


# ---------------------------------------------------------------------------------------------------- what the views show
def item_distance(item: ListItem, center: dict) -> float | None:
    if not item.has_store_location:
        return None
    return round(distance_km(center["lat"], center["lng"], item.StoreLatitude, item.StoreLongitude), 1)


def item_to_dict(item: ListItem, center: dict) -> dict:
    alt = alternative_of(item)
    return {
        "id": item.ListItemId,
        "name": item.ItemName,
        "barcode": item.BarCode,
        "category": item.Category,
        "unit_cost": str(money(item.UnitCost)),
        "quantity": item.ItemQuantity,
        "line_total": str(item.LineTotal),
        "store_name": item.StoreName,
        "store_address": item.StoreAddress,
        "image": item.ItemImage,
        "distance_km": item_distance(item, center),
        "alternative": ({**alt, "line_saving": str(item_saving(item))} if alt else None),
        "replaced": (item.CheaperAlternativeJSON or {}).get("replaced"),
        "collected": bool(item.IsCollected),
    }


def list_state(
    user: User,
    items: list[ListItem] | None = None,
    shopping_list: ShoppingList | None = None,
    position: budgets.BudgetPosition | None = None,
) -> dict:
    """The JSON the list API and the list page share: the list, its budget position and (optionally) its items."""
    budget = budgets.get_active_budget(user.UserId)
    shopping_list = shopping_list or budgets.get_active_list(budget)
    items = items if items is not None else budgets.list_items(shopping_list)
    position = position or (budgets.position(budget, shopping_list) if budget else None)
    center = student_center(user)
    return {
        "list": (
            {
                "id": shopping_list.ShoppingListId,
                "title": shopping_list.Title,
                "count": len(items),
                "total_cost": str(money(shopping_list.TotalCost)),
            }
            if shopping_list
            else None
        ),
        "budget": ({"title": budget.Title, **position.to_dict()} if budget and position else None),
        "over_budget": bool(position and position.is_over),
        "alert": over_budget_message(position) if position else None,
        "potential_savings": str(potential_savings(items)),
        "items": [item_to_dict(item, center) for item in items],
    }
