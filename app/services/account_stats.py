"""Numbers shown on the profile page."""

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select

from app.extensions import db
from app.models import Budget, ListItem
from app.utils.money import to_decimal

RECENT_PURCHASES = 5


@dataclass
class AccountStats:
    budget_count: int = 0
    active_budget: Budget | None = None
    total_spent: Decimal = Decimal("0.00")
    recent_purchases: list = field(default_factory=list)


def account_stats(user_id: str) -> AccountStats:
    """Budgets, the active budget, total spent, and the five most recent purchases.

    * *total spent* is what the student actually bought: the cost of every item marked purchased (a budget's
      ``UsedAmount`` also counts items still waiting on an open list, so it is not used here);
    * a *purchase* is an item marked ``IsPurchased`` (the trip was completed), newest first.
    """
    count = db.session.scalar(select(func.count()).select_from(Budget).where(Budget.UserId == user_id)) or 0
    spent = db.session.scalar(
        select(func.coalesce(func.sum(ListItem.UnitCost * ListItem.ItemQuantity), 0)).where(
            ListItem.UserId == user_id, ListItem.IsPurchased.is_(True)
        )
    )
    active = db.session.scalar(
        select(Budget).where(Budget.UserId == user_id, Budget.IsActive.is_(True)).order_by(Budget.DateCreated.desc())
    )
    purchases = db.session.scalars(
        select(ListItem)
        .where(ListItem.UserId == user_id, ListItem.IsPurchased.is_(True))
        .order_by(ListItem.PurchasedDate.desc(), ListItem.DateCreated.desc())
        .limit(RECENT_PURCHASES)
    ).all()
    return AccountStats(
        budget_count=count,
        active_budget=active,
        total_spent=to_decimal(spent).quantize(Decimal("0.01")),
        recent_purchases=purchases,
    )
