"""Admin changes to the built-in (mock) catalogue, which itself lives in code (``app/data/mock_products.py``).

``MockProductOverride``   one row per built-in product an admin changed: a new name, brand, category or reference
                          price, or ``Hidden`` (deleted: it no longer appears anywhere; Restore brings it back).
``MockRetailerOverride``  per product and chain (Checkers, Pick n Pay, ...): does that chain stock it, at what price,
                          and is it in stock. Empty fields mean "as the built-in catalogue works it out".
"""

from sqlalchemy import UniqueConstraint
from sqlalchemy import false as sa_false

from app.extensions import db
from app.utils.dates import utcnow


class MockProductOverride(db.Model):
    __tablename__ = "mock_product_overrides"

    Barcode = db.Column(db.String(50), primary_key=True)
    Hidden = db.Column(db.Boolean, nullable=False, default=False, server_default=sa_false())
    Name = db.Column(db.String(150), nullable=True)
    Brand = db.Column(db.String(80), nullable=True)
    Category = db.Column(db.String(20), nullable=True)
    Price = db.Column(db.Numeric(10, 2), nullable=True)  # the reference price the chains' prices are based on
    UpdatedBy = db.Column(db.String(100), nullable=True)
    UpdatedOn = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MockRetailerOverride(db.Model):
    __tablename__ = "mock_retailer_overrides"
    __table_args__ = (UniqueConstraint("Barcode", "Retailer", name="uq_mock_retailer"),)

    Id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    Barcode = db.Column(db.String(50), nullable=False, index=True)
    Retailer = db.Column(db.String(50), nullable=False)
    Listed = db.Column(db.Boolean, nullable=True)  # None: as built in; False: this chain does not stock it
    Price = db.Column(db.Numeric(10, 2), nullable=True)  # None: worked out from the reference price
    InStock = db.Column(db.Boolean, nullable=True)  # None: as built in
