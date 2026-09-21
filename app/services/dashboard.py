"""What the Dashboard (Step 8) shows: the budget, recent high-cost purchases, the list, potential savings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models import Budget, ListItem
from app.services import budgets, shopping
from app.services.retail_api import RetailAPIError, RetailConfigError, get_retail_provider
from app.utils.dates import utcnow
from app.utils.money import ZERO

RECENT_COUNT = 4


def recently_bought(user_id: str, now=None) -> list[ListItem]:
    """The four purchases the "Recently Bought (High Cost Items)" card shows.

    * Items bought at ``HIGH_COST_ITEM_THRESHOLD`` (R500) or more each: the four most recently bought.
    * When there are none, the four most expensive purchases (amount paid, quantity included) of the last
      ``HIGH_COST_FALLBACK_DAYS`` days: on a student budget few things cost R500, and the card should still say
      where the money went.
    """
    cfg = current_app.config
    purchased = select(ListItem).where(ListItem.UserId == user_id, ListItem.IsPurchased.is_(True))
    high = db.session.scalars(
        purchased.where(ListItem.UnitCost >= cfg["HIGH_COST_ITEM_THRESHOLD"])
        .order_by(ListItem.PurchasedDate.desc(), ListItem.ListItemId)
        .limit(RECENT_COUNT)
    ).all()
    if high:
        return list(high)
    since = (now or utcnow()) - timedelta(days=cfg["HIGH_COST_FALLBACK_DAYS"])
    recent = db.session.scalars(purchased.where(ListItem.PurchasedDate >= since)).all()
    recent.sort(key=lambda item: (item.LineTotal, item.PurchasedDate), reverse=True)
    return recent[:RECENT_COUNT]


@dataclass
class DashboardData:
    budget: Budget | None
    is_active: bool
    position: budgets.BudgetPosition | None
    recent: list[ListItem]
    list_count: int
    list_total: Decimal
    savings: Decimal
    has_alternatives: bool


def build(user) -> DashboardData:
    """Everything the dashboard page needs. Checking for cheaper alternatives is best-effort: a price-service
    outage just leaves the savings at what was last worked out."""
    active = budgets.get_active_budget(user.UserId)
    budget = active or budgets.last_closed_budget(user.UserId)
    shopping_list = budgets.get_active_list(active)
    items = budgets.list_items(shopping_list)
    if items:
        try:
            shopping.refresh_alternatives(items, get_retail_provider(), user)
        except (RetailAPIError, RetailConfigError):
            pass
    return DashboardData(
        budget=budget,
        is_active=active is not None,
        position=budgets.position(budget, shopping_list) if budget else None,
        recent=recently_bought(user.UserId),
        list_count=len(items),
        list_total=budgets.position(active, shopping_list).in_list if active else ZERO,
        savings=shopping.potential_savings(items),
        has_alternatives=any(shopping.alternative_of(item) for item in items),
    )
