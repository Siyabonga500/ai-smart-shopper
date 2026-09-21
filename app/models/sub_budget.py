"""SubBudget model - PDF pages 1-2 (fields) and page 19 (code).

``Category`` is one of Grocery / Toiletries / Clothes / Electronics, or
"Combined". Combined and the individual categories are mutually exclusive
within one Budget (PDF: Set Budget View, section D).
"""

from sqlalchemy import func

from app.extensions import db
from app.utils.ids import SUB_BUDGET_PREFIX, generate_id


class SubBudget(db.Model):
    __tablename__ = "sub_budgets"

    SubBudgetId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(SUB_BUDGET_PREFIX))
    UserId = db.Column(db.String(30), db.ForeignKey("users.UserId", ondelete="CASCADE"), nullable=False)
    BudgetId = db.Column(db.String(30), db.ForeignKey("budgets.BudgetId", ondelete="CASCADE"), nullable=False)
    Category = db.Column(db.String(50), nullable=False)  # e.g., Grocery, Toiletries, Clothes, Electronics, Combined
    AllocatedAmount = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    UsedAmount = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    IsActive = db.Column(db.Boolean, nullable=False, default=True)
    DateCreated = db.Column(db.DateTime(timezone=True), server_default=func.now())
    ClosedDate = db.Column(db.DateTime(timezone=True), nullable=True)

    # Relationships
    list_items = db.relationship("ListItem", backref="sub_budget", lazy=True, cascade="save-update, merge, delete")
