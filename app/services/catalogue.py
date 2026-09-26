"""Products added by admins (Admin > Products), and how they reach the students.

The app's retail provider (``retail_api.CachedProvider``, over LoyaltyHub or the offline catalogue) calls
:func:`matching` and :func:`by_code` on every search and barcode lookup, so students see the provider's offers *plus*
the admin products at stores near them. The admin rows are read from the database every time (never cached), so a new
product or a price change shows at once.
"""

from __future__ import annotations

import re

from sqlalchemy import or_, select

from app.extensions import db
from app.models import CatalogueProduct, Store
from app.models.catalogue import BUDGET_CATEGORY
from app.services.retail_api import CATEGORY_PLACEHOLDERS, PLACEHOLDER_IMAGE, Product
from app.utils.geo import distance_km
from app.utils.pagination import Page, like_pattern, paginate

MAX_CATALOGUE_ROWS = 200


# ---------------------------------------------------------------------------------------------- admin list
def search(query: str | None = None, category: str | None = None, store_id: str | None = None, page=1) -> Page:
    statement = select(CatalogueProduct).join(Store)
    text = (query or "").strip()
    if text:
        pattern = like_pattern(text)
        statement = statement.where(
            or_(
                CatalogueProduct.Name.ilike(pattern, escape="\\"),
                CatalogueProduct.Brand.ilike(pattern, escape="\\"),
                CatalogueProduct.Barcode.ilike(pattern, escape="\\"),
                CatalogueProduct.Sku.ilike(pattern, escape="\\"),
                Store.Name.ilike(pattern, escape="\\"),
            )
        )
    if category:
        statement = statement.where(CatalogueProduct.Category == category)
    if store_id:
        statement = statement.where(CatalogueProduct.StoreId == store_id)
    return paginate(statement.order_by(CatalogueProduct.Category, CatalogueProduct.Name), page, 20)


def get(product_id: str) -> CatalogueProduct | None:
    return db.session.get(CatalogueProduct, product_id)


def counts() -> dict[str, int]:
    from sqlalchemy import func

    rows = db.session.execute(select(CatalogueProduct.Category, func.count()).group_by(CatalogueProduct.Category)).all()
    return {category: count for category, count in rows}


def store_options() -> list[dict]:
    """Every store for the product form's drop-down and map, with what it sells."""
    return [
        {
            "id": s.StoreId,
            "name": s.Name,
            "brand": s.Brand,
            "suburb": s.Suburb or "",
            "address": s.Address,
            "lat": s.Latitude,
            "lng": s.Longitude,
            "type": s.StoreType or "grocery",
        }
        for s in db.session.scalars(select(Store).order_by(Store.Brand, Store.Name))
    ]


def snapshot(product: CatalogueProduct) -> dict:
    return {
        "category": product.Category,
        "name": product.Name,
        "brand": product.Brand,
        "sku": product.Sku,
        "barcode": product.Barcode,
        "price": str(product.Price),
        "sale_price": str(product.SalePrice) if product.SalePrice is not None else None,
        "image": product.ImageUrl,
        "photos": len(product.Photos or []),
        "size": product.Size,
        "colour": product.Colour,
        "stock": product.StockStatus,
        "quantity": product.StockQuantity,
        "store": product.StoreId,
    }


# ---------------------------------------------------------------------------------------------- to Product
def to_product(row: CatalogueProduct, lat: float | None = None, lng: float | None = None) -> Product:
    store = row.store
    category = row.budget_category
    distance = None
    if lat is not None and lng is not None:
        distance = round(distance_km(lat, lng, store.Latitude, store.Longitude), 2)
    details = ", ".join(v for v in (row.Size, row.Colour) if v) if row.is_clothing else ""
    return Product(
        name=f"{row.Name} ({details})" if details else row.Name,
        barcode=row.code,
        price=row.price_decimal,
        original_price=row.price_before_sale,
        images=tuple(row.photos),
        image_url=row.ImageUrl or CATEGORY_PLACEHOLDERS.get(category, PLACEHOLDER_IMAGE),
        store_name=store.Name,
        store_address=store.Address,
        store_lat=store.Latitude,
        store_lng=store.Longitude,
        category=category,
        in_stock=row.in_stock,
        brand=row.Brand,
        retailer=store.Brand,
        store_id=store.Slug,
        distance_km=distance,
        sku=row.Sku,
        size=row.Size,
        colour=row.Colour,
        stock_status=row.StockStatus,
        source="admin",
    )


def _near(rows, lat, lng, radius_km) -> list[Product]:
    products = [to_product(row, lat, lng) for row in rows]
    if lat is None or lng is None:
        return products
    return [p for p in products if p.distance_km is None or p.distance_km <= radius_km]


def matching(query: str | None, lat, lng, radius_km, category: str | None = None) -> list[Product]:
    tokens = re.findall(r"\w+", (query or "").casefold())
    statement = select(CatalogueProduct).join(Store)
    wanted = (category or "").strip()
    if wanted:
        raw = [name for name, budget in BUDGET_CATEGORY.items() if budget.casefold() == wanted.casefold()]
        if not raw:
            return []
        statement = statement.where(CatalogueProduct.Category.in_(raw))
    for token in tokens:
        pattern = like_pattern(token)
        statement = statement.where(
            or_(
                CatalogueProduct.Name.ilike(pattern, escape="\\"),
                CatalogueProduct.Brand.ilike(pattern, escape="\\"),
                CatalogueProduct.Colour.ilike(pattern, escape="\\"),
                CatalogueProduct.Barcode.ilike(pattern, escape="\\"),
                CatalogueProduct.Sku.ilike(pattern, escape="\\"),
                CatalogueProduct.Category.ilike(pattern, escape="\\"),
            )
        )
    rows = db.session.scalars(statement.limit(MAX_CATALOGUE_ROWS)).all()
    return _near(rows, lat, lng, radius_km)


def by_code(code: str | None, lat=None, lng=None, radius_km=15) -> list[Product]:
    code = (code or "").strip()
    if not code:
        return []
    rows = db.session.scalars(
        select(CatalogueProduct).where(or_(CatalogueProduct.Barcode == code, CatalogueProduct.Sku == code))
    ).all()
    return _near(rows, lat, lng, radius_km)


# ---------------------------------------------------------------------------------------------- pasted pictures
def pictures_for(barcodes) -> dict[str, list[str]]:
    """``{barcode: [url, ...]}`` for the barcodes that have pictures pasted in Admin > Pictures (in order)."""
    from app.models import ProductPicture

    codes = [b for b in barcodes if b]
    if not codes:
        return {}
    table: dict[str, list[str]] = {}
    rows = db.session.scalars(
        select(ProductPicture).where(ProductPicture.Barcode.in_(codes)).order_by(ProductPicture.Position)
    )
    for row in rows:
        table.setdefault(row.Barcode, []).append(row.Url)
    return table


def set_pictures(barcode: str, urls: list[str]) -> list[str]:
    """Replace the pasted pictures of ``barcode`` (an empty list removes them). The caller commits."""
    from app.models import ProductPicture

    db.session.query(ProductPicture).filter(ProductPicture.Barcode == barcode).delete()
    clean = list(dict.fromkeys(u for u in urls if u))
    for position, url in enumerate(clean, 1):
        db.session.add(ProductPicture(Barcode=barcode, Url=url, Position=position))
    return clean
