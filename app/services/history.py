"""Shopping history (Step 14): finished trips, their invoices, filters, and Reorder.

A *trip* is a shopping list that was closed with "Done - Purchase Completed": its items carry ``IsPurchased`` and
the list has a ``DateClosed``. Lists that were closed by removing the budget have no purchased items (they were
deleted), so they never show up here.

Reorder puts the same products back on the active list at *today's* prices, looked up again through the retail
provider (never copied from the old trip), and says what changed since last time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from flask import current_app
from sqlalchemy import func, select

from app.extensions import db
from app.models import ListItem, ShoppingList
from app.services import budgets, shopping
from app.services.retail_api import Product, RetailProvider
from app.utils.dates import sast_day_bounds, to_sast
from app.utils.money import ZERO, money

PAGE_SIZE = 10


# ------------------------------------------------------------------------------------------------ filters
@dataclass
class HistoryFilters:
    date_from: date | None = None
    date_to: date | None = None
    store: str | None = None
    category: str | None = None
    page: int = 1

    @property
    def active(self) -> bool:
        return any((self.date_from, self.date_to, self.store, self.category))


def parse_filters(args) -> tuple[HistoryFilters, dict[str, str]]:
    """Read ``?from=&to=&store=&category=&page=``. Bad values are ignored and reported as ``{field: message}``."""
    filters, errors = HistoryFilters(), {}
    for field_name, key in (("date_from", "from"), ("date_to", "to")):
        raw = (args.get(key) or "").strip()
        if raw:
            try:
                setattr(filters, field_name, date.fromisoformat(raw))
            except ValueError:
                errors[key] = "Use a date like 2026-09-30."
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        errors["to"] = "The end date must not be before the start date."
        filters.date_from = filters.date_to = None
    filters.store = " ".join((args.get("store") or "").split())[:100] or None
    filters.category = " ".join((args.get("category") or "").split())[:50] or None
    try:
        filters.page = max(1, int(args.get("page") or 1))
    except ValueError:
        filters.page = 1
    return filters, errors


# ------------------------------------------------------------------------------------------------ trips
@dataclass
class Trip:
    shopping_list: ShoppingList
    items: list[ListItem]

    @property
    def id(self) -> str:
        return self.shopping_list.ShoppingListId

    @property
    def title(self) -> str:
        return self.shopping_list.Title

    @property
    def closed_on(self):
        return self.shopping_list.DateClosed or self.shopping_list.DateCreated

    @property
    def total(self) -> Decimal:
        return money(sum((item.LineTotal for item in self.items), ZERO))

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def store_count(self) -> int:
        return len({(item.StoreName or "").casefold() for item in self.items})


@dataclass
class TripPage:
    trips: list[Trip]
    total: int
    page: int
    pages: int


def _purchased_list_ids(user_id: str, **conditions):
    query = select(ListItem.ShoppingListId).where(ListItem.UserId == user_id, ListItem.IsPurchased.is_(True))
    for column, value in conditions.items():
        query = query.where(getattr(ListItem, column) == value)
    return query


def _trips_for(lists: list[ShoppingList]) -> list[Trip]:
    if not lists:
        return []
    rows = db.session.scalars(
        select(ListItem)
        .where(ListItem.ShoppingListId.in_([sl.ShoppingListId for sl in lists]), ListItem.IsPurchased.is_(True))
        .order_by(ListItem.ItemName, ListItem.ListItemId)
    ).all()
    by_list: dict[str, list[ListItem]] = {}
    for item in rows:
        by_list.setdefault(item.ShoppingListId, []).append(item)
    return [Trip(sl, by_list.get(sl.ShoppingListId, [])) for sl in lists]


def find_trips(user_id: str, filters: HistoryFilters, page_size: int = PAGE_SIZE) -> TripPage:
    """The student's finished trips, newest first, narrowed by the filters (a store or category matches a trip that bought from it)."""
    closed = func.coalesce(ShoppingList.DateClosed, ShoppingList.DateCreated)
    query = select(ShoppingList).where(
        ShoppingList.UserId == user_id,
        ShoppingList.IsActive.is_(False),
        ShoppingList.ShoppingListId.in_(_purchased_list_ids(user_id)),
    )
    lower, upper = sast_day_bounds(filters.date_from, filters.date_to)
    if lower is not None:
        query = query.where(closed >= lower)
    if upper is not None:
        query = query.where(closed < upper)
    if filters.store:
        query = query.where(ShoppingList.ShoppingListId.in_(_purchased_list_ids(user_id, StoreName=filters.store)))
    if filters.category:
        query = query.where(ShoppingList.ShoppingListId.in_(_purchased_list_ids(user_id, Category=filters.category)))

    total = db.session.scalar(select(func.count()).select_from(query.subquery())) or 0
    pages = max(1, -(-total // page_size))
    page = min(filters.page, pages)
    lists = db.session.scalars(
        query.order_by(closed.desc(), ShoppingList.ShoppingListId.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    return TripPage(_trips_for(list(lists)), total, page, pages)


def get_trip(user_id: str, list_id: str) -> Trip | None:
    shopping_list = db.session.get(ShoppingList, list_id)
    if shopping_list is None or shopping_list.UserId != user_id or shopping_list.IsActive:
        return None
    trip = _trips_for([shopping_list])[0]
    return trip if trip.items else None


def filter_options(user_id: str) -> dict[str, list[str]]:
    """Stores and categories the student has bought from (for the filter drop-downs)."""

    def distinct(column):
        return [
            v
            for v in db.session.scalars(
                select(column)
                .where(ListItem.UserId == user_id, ListItem.IsPurchased.is_(True), column.is_not(None), column != "")
                .distinct()
                .order_by(column)
            )
            if v
        ]

    return {"stores": distinct(ListItem.StoreName), "categories": distinct(ListItem.Category)}

def store_points(user_id: str) -> list[dict]:
    """Return unique store locations used in the student's purchased history."""
    rows = db.session.execute(
        select(
            ListItem.StoreName,
            ListItem.StoreAddress,
            ListItem.StoreLatitude,
            ListItem.StoreLongitude,
            func.count(ListItem.ListItemId),
        )
        .where(
            ListItem.UserId == user_id,
            ListItem.IsPurchased.is_(True),
            ListItem.StoreLatitude.is_not(None),
            ListItem.StoreLongitude.is_not(None),
        )
        .group_by(ListItem.StoreName, ListItem.StoreAddress, ListItem.StoreLatitude, ListItem.StoreLongitude)
        .order_by(func.count(ListItem.ListItemId).desc(), ListItem.StoreName)
    ).all()
    return [
        {
            "name": name or "Store",
            "address": address or "",
            "lat": float(lat),
            "lng": float(lng),
            "purchases": int(count),
        }
        for name, address, lat, lng, count in rows
    ]


def group_by_month(trips: list[Trip]) -> list[tuple[str, list[Trip]]]:
    """``[("September 2026", [trips...]), ...]`` in the order given (newest first), by SAST month closed."""
    groups: list[tuple[str, list[Trip]]] = []
    for trip in trips:
        local = to_sast(trip.closed_on)
        label = local.strftime("%B %Y") if local else "Earlier"
        if groups and groups[-1][0] == label:
            groups[-1][1].append(trip)
        else:
            groups.append((label, [trip]))
    return groups


# ------------------------------------------------------------------------------------------------ reorder
@dataclass
class ReorderLine:
    name: str
    quantity: int
    old_price: Decimal
    new_price: Decimal | None
    old_store: str | None
    new_store: str | None

    @property
    def status(self) -> str:
        if self.new_price is None:
            return "unavailable"
        if self.new_price > self.old_price:
            return "up"
        if self.new_price < self.old_price:
            return "down"
        return "same"

    @property
    def moved(self) -> bool:
        return self.new_price is not None and (self.new_store or "") != (self.old_store or "")


@dataclass
class ReorderResult:
    lines: list[ReorderLine] = field(default_factory=list)
    position: budgets.BudgetPosition | None = None

    def count(self, status: str) -> int:
        return sum(1 for line in self.lines if line.status == status)

    @property
    def added(self) -> int:
        return sum(1 for line in self.lines if line.status != "unavailable")

    @property
    def unavailable(self) -> list[str]:
        return [line.name for line in self.lines if line.status == "unavailable"]

    def summary(self) -> str:
        """ "2 items increased in price, 1 decreased." (with what else changed since the last trip)."""
        up, down, unavailable = self.count("up"), self.count("down"), self.count("unavailable")
        moved = sum(1 for line in self.lines if line.moved)
        parts = []
        if up:
            parts.append(f"{up} item{'' if up == 1 else 's'} increased in price")
        if down:
            parts.append(f"{down} decreased" if up else f"{down} item{'' if down == 1 else 's'} decreased in price")
        if not up and not down and self.added:
            parts.append("No prices have changed")
        if moved:
            parts.append(f"{moved} moved to another store")
        text = ", ".join(parts)
        text = (text + ".") if text else ""
        if unavailable:
            names = ", ".join(self.unavailable)
            text += f" {unavailable} item{' is' if unavailable == 1 else 's are'} no longer available and {'was' if unavailable == 1 else 'were'} left out ({names})."
        return text.strip()


def current_offer(provider: RetailProvider, item: ListItem, center: dict, radius: float) -> Product | None:
    """Today's offer for what was bought: the same store if it still has it in stock, else the cheapest other store."""
    if item.BarCode:
        offers = provider.get_offers_by_barcode(item.BarCode, center["lat"], center["lng"], radius)
    else:
        offers = [
            o
            for o in provider.search_products(item.ItemName, center["lat"], center["lng"], radius)
            if o.name.casefold() == (item.ItemName or "").casefold()
        ]
    in_stock = [o for o in offers if o.in_stock]
    if not in_stock:
        return None
    same_store = [o for o in in_stock if (o.store_name or "") == (item.StoreName or "")]
    pool = same_store or in_stock
    return min(pool, key=lambda o: (o.price, o.distance_km if o.distance_km is not None else 999, o.store_name or ""))


def reorder(provider: RetailProvider, user, trip: Trip, replace: bool) -> ReorderResult:
    """Look every item up again first (network), then change the list in one transaction (see shopping.add_offers)."""
    center = shopping.student_center(user)
    radius = current_app.config["SEARCH_MAX_RADIUS_KM"]
    result, entries = ReorderResult(), []
    for item in trip.items:
        offer = current_offer(provider, item, center, radius)
        result.lines.append(
            ReorderLine(
                item.ItemName,
                item.ItemQuantity,
                money(item.UnitCost),
                money(offer.price) if offer else None,
                item.StoreName,
                offer.store_name if offer else None,
            )
        )
        if offer is not None:
            entries.append((offer, item.ItemQuantity))
    if not entries:
        raise shopping.ShoppingError(
            "None of the items from that trip can be found at the stores near you right now.", 409
        )
    result.position = shopping.add_offers(user, entries, replace=replace)
    return result
