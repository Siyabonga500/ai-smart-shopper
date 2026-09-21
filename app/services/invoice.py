"""Turn list items into an invoice: rows grouped by store, with subtotals (Steps 13 and 14).

The same code builds the Shopping List Summary (items still on the active list) and the read-only invoice of a
finished trip (items marked purchased), so the two screens always agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.models import ListItem
from app.utils.money import ZERO, money

UNKNOWN_STORE = "Store not recorded"


@dataclass
class InvoiceGroup:
    store_name: str
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    rows: list[dict] = field(default_factory=list)
    subtotal: Decimal = ZERO

    @property
    def item_count(self) -> int:
        return len(self.rows)

    @property
    def units(self) -> int:
        return sum(row["quantity"] for row in self.rows)

    @property
    def located(self) -> bool:
        return self.lat is not None and self.lng is not None


@dataclass
class Invoice:
    groups: list[InvoiceGroup]
    total: Decimal
    item_count: int
    units: int

    @property
    def store_count(self) -> int:
        return len(self.groups)

    @property
    def rows(self) -> list[dict]:
        return [row for group in self.groups for row in group.rows]


def build_invoice(items: list[ListItem], order: list[str] | None = None) -> Invoice:
    """Group ``items`` by store. ``order`` (store names) puts those stores first, in that order; the rest follow A to Z."""
    groups: dict[str, InvoiceGroup] = {}
    for item in items:
        name = (item.StoreName or "").strip() or UNKNOWN_STORE
        group = groups.setdefault(name.casefold(), InvoiceGroup(store_name=name))
        if group.address is None and item.StoreAddress:
            group.address = item.StoreAddress
        if not group.located and item.has_store_location:
            group.lat, group.lng = item.StoreLatitude, item.StoreLongitude
        line_total = item.LineTotal
        group.rows.append(
            {
                "id": item.ListItemId,
                "name": item.ItemName,
                "quantity": item.ItemQuantity,
                "store_name": name,
                "unit_cost": money(item.UnitCost),
                "line_total": line_total,
                "category": item.Category,
                "barcode": item.BarCode,
                "image": item.ItemImage,
            }
        )
        group.subtotal += line_total

    position = {name.casefold(): index for index, name in enumerate(order or [])}
    ordered = sorted(groups.items(), key=lambda pair: (position.get(pair[0], len(position)), pair[0]))
    result = [group for _, group in ordered]
    for group in result:
        group.rows.sort(key=lambda row: row["name"].casefold())
    total = sum((group.subtotal for group in result), ZERO)
    return Invoice(result, money(total), sum(g.item_count for g in result), sum(g.units for g in result))
