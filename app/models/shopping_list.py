"""ShoppingList model - PDF page 2 (fields) and pages 19-20 (code).

Added on top of the PDF: ``activate()`` / ``deactivate()`` (closing stamps
``DateClosed``) and cascade deletes to its items.
"""

from sqlalchemy import func

from app.extensions import db
from app.models.mixins import ActivatableMixin
from app.utils.ids import SHOPPING_LIST_PREFIX, generate_id


class ShoppingList(ActivatableMixin, db.Model):
    __tablename__ = "shopping_lists"

    closed_field = "DateClosed"

    ShoppingListId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(SHOPPING_LIST_PREFIX))
    UserId = db.Column(db.String(30), db.ForeignKey("users.UserId", ondelete="CASCADE"), nullable=False)
    BudgetId = db.Column(db.String(30), db.ForeignKey("budgets.BudgetId", ondelete="CASCADE"), nullable=False)
    Title = db.Column(db.String(100), nullable=False)
    TotalCost = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    IsActive = db.Column(db.Boolean, nullable=False, default=True)
    DateCreated = db.Column(db.DateTime(timezone=True), server_default=func.now())
    DateClosed = db.Column(db.DateTime(timezone=True), nullable=True)

    # Relationships
    list_items = db.relationship("ListItem", backref="shopping_list", lazy=True, cascade="save-update, merge, delete")
