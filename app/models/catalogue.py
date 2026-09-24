"""Products an admin adds by hand (Admin > Products).

Each row is one product at one store, so it slots straight into search, price comparison and shopping lists next to
the prices that come from LoyaltyHub (see :mod:`app.services.catalogue`).

Categories follow the admin form: ``Grocery`` and ``Toiletries`` need a barcode and one image; ``Clothing`` has a
brand, SKU, size, colour and several photos instead. The budget category for clothing is ``Clothes`` (the name the
budgets already use), see :data:`BUDGET_CATEGORY`.
"""

from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.utils.dates import utcnow
from app.utils.ids import PRODUCT_PREFIX, generate_id
from app.utils.money import to_decimal

PRODUCT_CATEGORIES = ("Grocery", "Toiletries", "Clothing")
BUDGET_CATEGORY = {"Grocery": "Grocery", "Toiletries": "Toiletries", "Clothing": "Clothes"}
STOCK_STATUSES = (("in_stock", "In stock"), ("low_stock", "Low stock"), ("out_of_stock", "Out of stock"))
STOCK_LABELS = dict(STOCK_STATUSES)
CLOTHING_SIZES = ("XS", "S", "M", "L", "XL", "XXL", "3XL", "One size")


class CatalogueProduct(db.Model):
    __tablename__ = "catalogue_products"

    ProductId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(PRODUCT_PREFIX))
    Category = db.Column(db.String(20), nullable=False, index=True)  # Grocery | Toiletries | Clothing
    Name = db.Column(db.String(150), nullable=False)
    Brand = db.Column(db.String(80), nullable=True)
    Sku = db.Column(db.String(50), nullable=True, index=True)  # clothing
    Barcode = db.Column(db.String(50), nullable=True, index=True)  # grocery and toiletries
    Price = db.Column(db.Numeric(10, 2), nullable=False)
    ImageUrl = db.Column(db.String(1000), nullable=True)  # the main picture
    Photos = db.Column(db.JSON, nullable=True)  # clothing: every photo, the first one is also ImageUrl
    Size = db.Column(db.String(30), nullable=True)
    Colour = db.Column(db.String(40), nullable=True)
    StockStatus = db.Column(db.String(20), nullable=False, default="in_stock")
    StockQuantity = db.Column(db.Integer, nullable=True)
    StoreId = db.Column(db.String(30), db.ForeignKey("stores.StoreId", ondelete="CASCADE"), nullable=False, index=True)
    CreatedBy = db.Column(db.String(100), nullable=True)  # the admin's e-mail
    CreatedOn = db.Column(db.DateTime(timezone=True), server_default=func.now())
    UpdatedOn = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    store = db.relationship("Store", backref=db.backref("catalogue_products", passive_deletes=True))

    @property
    def is_clothing(self) -> bool:
        return self.Category == "Clothing"

    @property
    def in_stock(self) -> bool:
        return self.StockStatus != "out_of_stock"

    @property
    def stock_label(self) -> str:
        return STOCK_LABELS.get(self.StockStatus, self.StockStatus)

    @property
    def budget_category(self) -> str:
        return BUDGET_CATEGORY.get(self.Category, self.Category)

    @property
    def code(self) -> str | None:
        """What identifies the product across stores: the barcode, or the SKU for clothing."""
        return self.Barcode or self.Sku

    @property
    def price_decimal(self) -> Decimal:
        return to_decimal(self.Price)

    @property
    def photos(self) -> list[str]:
        photos = [p for p in (self.Photos or []) if isinstance(p, str) and p]
        if self.ImageUrl and self.ImageUrl not in photos:
            photos.insert(0, self.ImageUrl)
        return photos
