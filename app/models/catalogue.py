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
from app.utils.ids import PICTURE_PREFIX, PRODUCT_PREFIX, generate_id
from app.utils.money import to_decimal

PRODUCT_CATEGORIES = ("Grocery", "Toiletries", "Clothing")
BUDGET_CATEGORY = {"Grocery": "Grocery", "Toiletries": "Toiletries", "Clothing": "Clothes"}
STOCK_STATUSES = (("in_stock", "In stock"), ("low_stock", "Low stock"), ("out_of_stock", "Out of stock"))
STOCK_LABELS = dict(STOCK_STATUSES)
CLOTHING_SIZES = ("XS", "S", "M", "L", "XL", "XXL", "3XL", "One size")


def sale_percent(original, now) -> int:
    """How much cheaper ``now`` is than ``original``, in whole percent (R300 -> R250 is 17%)."""
    original, now = to_decimal(original), to_decimal(now)
    if original <= 0 or now >= original:
        return 0
    return int(((original - now) / original * 100).quantize(Decimal("1"), rounding="ROUND_HALF_UP"))


class ProductPicture(db.Model):
    """Picture addresses an admin pasted for a product by barcode (Admin > Pictures). They are shown for that barcode
    whatever the price source (the built-in catalogue has no pictures of its own). Several per product: in order."""

    __tablename__ = "product_pictures"

    PictureId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(PICTURE_PREFIX))
    Barcode = db.Column(db.String(50), nullable=False, index=True)
    Url = db.Column(db.String(1000), nullable=False)
    Position = db.Column(db.Integer, nullable=False, default=0)
    CreatedOn = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class CatalogueProduct(db.Model):
    __tablename__ = "catalogue_products"

    ProductId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(PRODUCT_PREFIX))
    Category = db.Column(db.String(20), nullable=False, index=True)  # Grocery | Toiletries | Clothing
    Name = db.Column(db.String(150), nullable=False)
    Brand = db.Column(db.String(80), nullable=True)
    Sku = db.Column(db.String(50), nullable=True, index=True)  # clothing
    Barcode = db.Column(db.String(50), nullable=True, index=True)  # grocery and toiletries
    Price = db.Column(db.Numeric(10, 2), nullable=False)
    SalePrice = db.Column(db.Numeric(10, 2), nullable=True)  # on sale: what students pay now ("Was" Price, "Now" this)
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
        """What a student pays now: the sale price while there is one."""
        return to_decimal(self.SalePrice) if self.on_sale else to_decimal(self.Price)

    @property
    def on_sale(self) -> bool:
        return self.SalePrice is not None and to_decimal(self.SalePrice) < to_decimal(self.Price)

    @property
    def price_before_sale(self) -> Decimal | None:
        return to_decimal(self.Price) if self.on_sale else None

    @property
    def sale_percent(self) -> int:
        return sale_percent(self.Price, self.SalePrice) if self.on_sale else 0

    @property
    def photos(self) -> list[str]:
        photos = [p for p in (self.Photos or []) if isinstance(p, str) and p]
        if self.ImageUrl and self.ImageUrl not in photos:
            photos.insert(0, self.ImageUrl)
        return photos
