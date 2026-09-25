"""ListItem model - PDF pages 2-3 (fields) and page 21 (code).

``BarCode`` is what groups identical products across stores for price
comparison; ``CheaperAlternativeJSON`` holds the alternative shown by the
"Replace" button on the Shopping List view.

Steps 8-14 needed more than the PDF has:
  * ``Category`` (Grocery / Toiletries / ...): which budget category the item counts against, and what
    the history filter uses.
  * ``StoreLatitude`` / ``StoreLongitude``: where the store is, for the distance shown on the list and
    for the shopping route (the PDF only keeps the address text).
  * ``IsPurchased`` / ``PurchasedDate``: set by "Done - Purchase Completed"; this is what turns a list
    item into history ("Recently Bought", the History view, the profile's last purchases).

``CheaperAlternativeJSON`` layout (all keys optional)::

    {"alternative": {...cheaper offer...} | null,   # what the Replace button would switch to
     "replaced":    {...the product this item used to be...} | null,
     "checked_at":  "2026-09-20T18:00:00+00:00"}
"""

from decimal import Decimal

from sqlalchemy import false as sa_false
from sqlalchemy import func

from app.extensions import db
from app.utils.ids import LIST_ITEM_PREFIX, generate_id
from app.utils.money import to_decimal


class ListItem(db.Model):
    __tablename__ = "list_items"

    ListItemId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(LIST_ITEM_PREFIX))
    UserId = db.Column(db.String(30), db.ForeignKey("users.UserId", ondelete="CASCADE"), nullable=False)
    ShoppingListId = db.Column(
        db.String(30), db.ForeignKey("shopping_lists.ShoppingListId", ondelete="CASCADE"), nullable=False
    )
    SubBudgetId = db.Column(db.String(30), db.ForeignKey("sub_budgets.SubBudgetId", ondelete="CASCADE"), nullable=True)
    ItemName = db.Column(db.String(150), nullable=False)
    UnitCost = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    ItemQuantity = db.Column(db.Integer, nullable=False, default=1)
    StoreName = db.Column(db.String(100), nullable=True)
    ItemImage = db.Column(db.String(1000), nullable=True)
    BarCode = db.Column(db.String(50), nullable=True)
    StoreAddress = db.Column(db.Text, nullable=True)
    StoreLink = db.Column(db.String(255), nullable=True)  # unused: students are never sent to a retailer's website
    CheaperAlternativeJSON = db.Column(db.JSON, nullable=True)  # Stores cheaper alternatives data
    DateCreated = db.Column(db.DateTime(timezone=True), server_default=func.now())

    # --- Steps 8-14 ------------------------------------------------------------------------------
    Category = db.Column(db.String(50), nullable=True)
    StoreLatitude = db.Column(db.Float, nullable=True)
    StoreLongitude = db.Column(db.Float, nullable=True)
    IsPurchased = db.Column(db.Boolean, nullable=False, default=False, server_default=sa_false())
    PurchasedDate = db.Column(db.DateTime(timezone=True), nullable=True)
    # Ticked off in the shop with the item's "Purchased" button, before "Done - Purchase Completed" closes the list.
    IsCollected = db.Column(db.Boolean, nullable=False, default=False, server_default=sa_false())

    # Computed property for total item cost (an exact Decimal, same as LineTotal)
    @property
    def TotalItemCost(self) -> Decimal:
        return self.LineTotal

    @property
    def LineTotal(self) -> Decimal:
        """``UnitCost * ItemQuantity`` as an exact Decimal (money is never summed as floats)."""
        return (to_decimal(self.UnitCost) * int(self.ItemQuantity or 0)).quantize(Decimal("0.01"))

    @property
    def has_store_location(self) -> bool:
        return self.StoreLatitude is not None and self.StoreLongitude is not None
