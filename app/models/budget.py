"""Budget model - PDF page 1 (fields) and page 18 (code).

Added on top of the PDF: ``remaining`` and ``percent_used`` properties (the
Dashboard, Budget and Shopping List views all show them), the
``activate()`` / ``deactivate()`` helpers, and cascade deletes.

Amounts may legitimately go negative or above 100%: the R1 750 NSFAS
reference and an over-budget list are warnings, never blocks, so neither
property clamps its result.
"""

from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models.mixins import ActivatableMixin
from app.utils.ids import BUDGET_PREFIX, generate_id
from app.utils.money import to_decimal


class Budget(ActivatableMixin, db.Model):
    __tablename__ = "budgets"

    closed_field = "ClosedDate"

    BudgetId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(BUDGET_PREFIX))
    UserId = db.Column(db.String(30), db.ForeignKey("users.UserId", ondelete="CASCADE"), nullable=False)
    Title = db.Column(db.String(100), nullable=False)
    TotalAmount = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    UsedAmount = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    IsActive = db.Column(db.Boolean, nullable=False, default=True)
    DateCreated = db.Column(db.DateTime(timezone=True), server_default=func.now())
    ClosedDate = db.Column(db.DateTime(timezone=True), nullable=True)

    # Relationships - deleting a budget deletes its sub-budgets and shopping
    # lists (and, through the lists, their items).
    sub_budgets = db.relationship("SubBudget", backref="budget", lazy=True, cascade="save-update, merge, delete")
    shopping_lists = db.relationship("ShoppingList", backref="budget", lazy=True, cascade="save-update, merge, delete")

    @property
    def remaining(self) -> Decimal:
        """``TotalAmount - UsedAmount``; negative when the budget is overspent."""
        return to_decimal(self.TotalAmount) - to_decimal(self.UsedAmount)

    @property
    def percent_used(self) -> float:
        """``UsedAmount / TotalAmount`` as a percentage (2 dp); 0 when there is no total.

        Not capped at 100: the Shopping List view shows "103% of budget used".
        """
        total = to_decimal(self.TotalAmount)
        if total <= 0:
            return 0.0
        return float(round(to_decimal(self.UsedAmount) / total * 100, 2))
