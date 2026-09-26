"""Several pictures per product, pasted pictures for the built-in catalogue, and sale prices (Was / Now / % off)."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, CatalogueProduct, ProductPicture, Store
from app.models.catalogue import sale_percent
from app.services import admin_stores
from app.services.retail_api import get_retail_provider

D = Decimal
HOME = {"lat": -29.8587, "lng": 31.0218}
MILK = "2000000000275"


def test_sale_percent_is_worked_out_on_the_original_price():
    assert sale_percent(D("300"), D("250")) == 17
    assert sale_percent(D("100"), D("75")) == 25
    assert sale_percent(D("100"), D("100")) == 0 and sale_percent(D("100"), D("120")) == 0


@pytest.fixture
def stores(app):
    admin_stores.add_known_stores()
    db.session.commit()
    return {s.Slug: s for s in db.session.scalars(select(Store))}


@pytest.fixture
def boss(client, make_user, login):
    login(make_user(email="boss@example.com", IsAdmin=True))
    return client


def clothing(store, **extra):
    data = {
        "Category": "Clothing",
        "StoreId": store.StoreId,
        "Name": "RJ40 Golfer",
        "Brand": "RJ",
        "Sku": "RJ-40",
        "Size": "S",
        "Colour": "White",
        "Price": "300",
        "StockStatus": "in_stock",
        "StockQuantity": "5",
        "PhotoUrls": "https://cdn.example.com/front.jpg\nhttps://cdn.example.com/back.jpg\nhttps://cdn.example.com/side.jpg",
    }
    data.update(extra)
    return data


def test_admin_product_with_a_sale_price_and_three_pictures(boss, stores, make_user, login, client):
    store = stores["mrprice-workshop"]
    too_high = boss.post("/admin/products/new", data=clothing(store, SalePrice="300"))
    assert too_high.status_code == 400 and "must be lower than the normal price" in too_high.get_data(as_text=True)

    assert boss.post("/admin/products/new", data=clothing(store, SalePrice="250")).status_code == 302
    product = db.session.scalar(select(CatalogueProduct))
    assert product.on_sale and product.sale_percent == 17 and product.price_decimal == D("250")
    assert len(product.photos) == 3
    assert "-17%" in boss.get("/admin/products").get_data(as_text=True)

    login(make_user(Latitude=HOME["lat"], Longitude=HOME["lng"]))
    card = next(c for c in client.get("/api/search?q=golfer").get_json()["results"] if c["sku"] == "RJ-40")
    assert card["price"] == "250.00" and card["original_price"] == "300.00"
    assert card["sale_percent"] == 17 and card["sale_saving"] == "50.00"
    assert card["images"] == [
        "https://cdn.example.com/front.jpg",
        "https://cdn.example.com/back.jpg",
        "https://cdn.example.com/side.jpg",
    ]


def test_grocery_products_can_have_several_pictures_too(boss, stores):
    data = {
        "Category": "Grocery",
        "StoreId": stores["checkers-musgrave"].StoreId,
        "Name": "Rice 2kg",
        "Barcode": "6001000000123",
        "Price": "45",
        "StockStatus": "in_stock",
        "PhotoUrls": "https://cdn.example.com/r1.jpg\nhttps://cdn.example.com/r2.jpg",
    }
    assert boss.post("/admin/products/new", data=data).status_code == 302
    assert db.session.scalar(select(CatalogueProduct)).photos == [
        "https://cdn.example.com/r1.jpg",
        "https://cdn.example.com/r2.jpg",
    ]


def test_pasted_pictures_replace_the_built_in_placeholder_everywhere(boss, make_user, login, client, stores):
    page = boss.get("/admin/pictures?q=clover").get_data(as_text=True)
    assert "Clover" in page and 'name="urls"' in page

    bad = boss.post("/admin/pictures", data={"barcode": MILK, "urls": "not a url"}, follow_redirects=True)
    assert "must be a picture address" in bad.get_data(as_text=True)
    boss.post(
        "/admin/pictures",
        data={"barcode": MILK, "urls": "https://img.example.com/milk-front.jpg\nhttps://img.example.com/milk-back.jpg"},
    )
    assert [p.Url for p in db.session.scalars(select(ProductPicture).order_by(ProductPicture.Position))] == [
        "https://img.example.com/milk-front.jpg",
        "https://img.example.com/milk-back.jpg",
    ]
    assert db.session.scalar(select(AuditLog).where(AuditLog.Action == "picture.set")) is not None

    offers = get_retail_provider().get_offers_by_barcode(MILK, HOME["lat"], HOME["lng"], 15)
    assert offers and all(
        o.image_url == "https://img.example.com/milk-front.jpg" and len(o.images) == 2 for o in offers
    )

    boss.post("/admin/pictures", data={"barcode": MILK, "urls": ""})  # empty: removed again
    assert db.session.query(ProductPicture).count() == 0
    assert all(
        o.image_url.startswith("/static/")
        for o in get_retail_provider().get_offers_by_barcode(MILK, HOME["lat"], HOME["lng"], 15)
    )


def test_compare_window_lets_you_pick_a_store_and_shows_pictures():
    source = open("app/static/js/search.js", encoding="utf-8").read()
    assert "function detailsEl" in source and "select(offer, row)" in source  # clicking a store swaps the details
    assert "gallery-btn prev" in source and "gallery-btn next" in source  # arrows when there is more than one picture
    assert '"Was "' in source and '"Now"' in source  # sale prices
