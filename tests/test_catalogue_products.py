"""Admin > Products (products added by hand), the built-in admin account, Durban stores, and the history trip maps."""

import io
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.data.durban_stores import ALL_STORES, CLOTHING_STORES
from app.extensions import db
from app.models import AuditLog, CatalogueProduct, ListItem, Store, User
from app.services import admin_stores, budgets, catalogue, shopping
from app.services.retail_api import get_retail_provider
from app.utils.dates import utcnow

D = Decimal
HOME = {"lat": -29.8587, "lng": 31.0218}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def sign_in(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = user.get_id()
        session["_fresh"] = True
    return client


@pytest.fixture
def stores(app):
    admin_stores.add_known_stores()
    db.session.commit()
    return {s.Slug: s for s in db.session.scalars(select(Store))}


@pytest.fixture
def admin(make_user):
    return make_user(first="Ada", last="Admin", email="ada@example.com", IsAdmin=True)


@pytest.fixture
def boss(app, admin):
    return sign_in(app.test_client(), admin)


@pytest.fixture
def student(make_user):
    return make_user(Latitude=HOME["lat"], Longitude=HOME["lng"], ResidentialAddress="Berea, Durban")


def grocery_form(store, **extra):
    data = {
        "Category": "Grocery",
        "StoreId": store.StoreId,
        "Name": "Clover Full Cream Milk 2L",
        "Brand": "Clover",
        "Barcode": "6001299001234",
        "Price": "34.99",
        "ImageUrl": "https://cdn.example.com/milk.jpg",
        "StockStatus": "in_stock",
    }
    data.update(extra)
    return data


def clothing_form(store, **extra):
    data = {
        "Category": "Clothing",
        "StoreId": store.StoreId,
        "Name": "Crew Neck T-Shirt",
        "Brand": "RSQ",
        "Sku": "MRP-TSH-0012",
        "Size": "M",
        "Colour": "Black",
        "Price": "79.99",
        "StockStatus": "in_stock",
        "StockQuantity": "12",
        "PhotoUrls": "https://cdn.example.com/front.jpg\nhttps://cdn.example.com/back.jpg",
    }
    data.update(extra)
    return data


# ------------------------------------------------------------------------------------------------ stores
def test_at_least_twenty_durban_stores_both_grocery_and_clothing():
    assert len(ALL_STORES) >= 20
    assert len({s.slug for s in ALL_STORES}) == len(ALL_STORES)
    assert {s.kind for s in ALL_STORES} == {"grocery", "clothing", "both"}
    assert len(CLOTHING_STORES) >= 10
    for seed in ALL_STORES:  # all around Durban
        assert -30.1 < seed.lat < -29.6 and 30.8 < seed.lng < 31.2, seed.slug


def test_admin_can_load_the_known_stores_once(boss, app):
    assert admin_stores.missing_seeds() == len(ALL_STORES)
    response = boss.post("/admin/stores/seed")
    assert response.status_code == 302
    assert db.session.scalar(select(db.func.count()).select_from(Store)) == len(ALL_STORES)
    assert admin_stores.missing_seeds() == 0
    boss.post("/admin/stores/seed")  # again: nothing added
    assert db.session.scalar(select(db.func.count()).select_from(Store)) == len(ALL_STORES)
    page = boss.get("/admin/stores?type=clothing").get_data(as_text=True)
    assert "Mr Price Gateway" in page and "Checkers Gateway" not in page


# ------------------------------------------------------------------------------------------------ products
def test_product_form_lists_the_stores_with_their_addresses_and_a_map(boss, stores):
    html = boss.get("/admin/products/new").get_data(as_text=True)
    assert 'id="product-store-map"' in html and "admin_products.js" in html
    assert "Gateway Theatre of Shopping, 1 Palm Boulevard" in html  # the address is in the drop-down
    for label in ("Grocery", "Toiletries", "Clothing"):
        assert f'value="{label}"' in html


def test_admin_adds_a_grocery_product_and_students_find_it(boss, stores, student, client):
    store = stores["checkers-musgrave"]
    response = boss.post("/admin/products/new", data=grocery_form(store))
    assert response.status_code == 302
    product = db.session.scalar(select(CatalogueProduct))
    assert product.Barcode == "6001299001234" and product.Price == D("34.99") and product.StoreId == store.StoreId
    assert db.session.scalar(select(AuditLog).where(AuditLog.Action == "product.create")) is not None

    listing = boss.get("/admin/products").get_data(as_text=True)
    assert "Clover Full Cream Milk 2L" in listing and "https://cdn.example.com/milk.jpg" in listing

    sign_in(client, student)
    data = client.get("/api/search?q=clover full cream").get_json()
    cards = [
        c for c in data["results"] if c["store_name"] == "Checkers Musgrave Centre" and c["barcode"] == "6001299001234"
    ]
    assert cards, data["results"][:3]
    card = cards[0]
    assert card["price"] == "34.99" and card["retailer"] == "Checkers" and card["in_stock"] is True
    assert card["image_url"] == "https://cdn.example.com/milk.jpg" and card["stock_status"] == "in_stock"

    details = client.get("/api/product?barcode=6001299001234&store_name=Checkers Musgrave Centre").get_json()
    assert details["product"]["store_name"] == "Checkers Musgrave Centre"


def test_admin_product_can_go_on_a_students_list(app, stores, student):
    store = stores["checkers-musgrave"]
    db.session.add(
        CatalogueProduct(
            Category="Toiletries",
            Name="Colgate 100ml",
            Barcode="6001067021234",
            Price=D("22.50"),
            StoreId=store.StoreId,
        )
    )
    db.session.commit()
    budgets.create_budget(student.UserId, "Sep", [("Toiletries", D("200"))])
    offer = shopping.resolve_offer(get_retail_provider(), student, barcode="6001067021234", store_name=store.Name)
    assert offer is not None and offer.category == "Toiletries"
    item = shopping.add_product(student, offer).item
    assert item.StoreName == store.Name and item.UnitCost == D("22.50") and item.StoreLatitude == store.Latitude


def test_grocery_needs_a_barcode_and_a_supermarket(boss, stores):
    no_barcode = boss.post("/admin/products/new", data=grocery_form(stores["checkers-musgrave"], Barcode=""))
    assert no_barcode.status_code == 400 and "Enter the barcode" in no_barcode.get_data(as_text=True)
    clothing_shop = boss.post("/admin/products/new", data=grocery_form(stores["mrprice-gateway"]))
    assert clothing_shop.status_code == 400 and "is a clothing store" in clothing_shop.get_data(as_text=True)
    assert db.session.scalar(select(CatalogueProduct)) is None


def test_clothing_has_its_own_fields(boss, stores, student, client):
    store = stores["mrprice-workshop"]  # central Durban: inside the student's default 5 km search radius
    missing = boss.post("/admin/products/new", data=clothing_form(store, Sku="", Size="", Colour="", Brand=""))
    text = missing.get_data(as_text=True)
    assert missing.status_code == 400
    for message in ("Enter the SKU", "Enter the size", "Enter the colour", "Enter the brand"):
        assert message in text

    supermarket = boss.post("/admin/products/new", data=clothing_form(stores["checkers-musgrave"]))
    assert supermarket.status_code == 400 and "does not sell clothing" in supermarket.get_data(as_text=True)

    assert boss.post("/admin/products/new", data=clothing_form(store)).status_code == 302
    product = db.session.scalar(select(CatalogueProduct))
    assert (product.Sku, product.Size, product.Colour, product.Brand) == ("MRP-TSH-0012", "M", "Black", "RSQ")
    assert product.photos == ["https://cdn.example.com/front.jpg", "https://cdn.example.com/back.jpg"]
    assert product.StockQuantity == 12 and product.budget_category == "Clothes"

    sign_in(client, student)
    cards = client.get("/api/search?category=Clothes&q=crew neck").get_json()["results"]
    shirt = next(c for c in cards if c["sku"] == "MRP-TSH-0012")
    assert shirt["name"] == "Crew Neck T-Shirt (M, Black)" and shirt["size"] == "M" and shirt["colour"] == "Black"
    assert shirt["retailer"] == "Mr Price" and shirt["category"] == "Clothes"


def test_clothing_photos_can_be_uploaded(boss, stores, app, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    data = clothing_form(stores["mrprice-gateway"], PhotoUrls="")
    data["Photos"] = [(io.BytesIO(PNG), "front.png"), (io.BytesIO(PNG), "back.png")]
    response = boss.post("/admin/products/new", data=data, content_type="multipart/form-data")
    assert response.status_code == 302
    product = db.session.scalar(select(CatalogueProduct))
    assert len(product.photos) == 2 and all(p.startswith("/static/uploads/products/") for p in product.photos)
    assert len(list((tmp_path / "products").iterdir())) == 2


def test_zero_clothing_stock_is_out_of_stock_and_edit_and_delete(boss, stores):
    boss.post("/admin/products/new", data=clothing_form(stores["mrprice-gateway"], StockQuantity="0"))
    product = db.session.scalar(select(CatalogueProduct))
    assert product.StockStatus == "out_of_stock" and not product.in_stock

    page = boss.get(f"/admin/products/{product.ProductId}/edit").get_data(as_text=True)
    assert "MRP-TSH-0012" in page
    response = boss.post(
        f"/admin/products/{product.ProductId}/edit", data=clothing_form(stores["edgars-gateway"], Price="99.00")
    )
    assert response.status_code == 302
    db.session.refresh(product)
    assert product.Price == D("99.00") and product.StoreId == stores["edgars-gateway"].StoreId

    assert boss.post(f"/admin/products/{product.ProductId}/delete").status_code == 302
    assert db.session.scalar(select(CatalogueProduct)) is None


def test_students_cannot_open_the_product_pages(client, student):
    sign_in(client, student)
    assert client.get("/admin/products").status_code == 403
    assert client.get("/admin/products/new").status_code == 403


def test_catalogue_search_respects_distance(app, stores):
    far = stores["shoprite-umlazi-mega-city"]
    db.session.add(
        CatalogueProduct(
            Category="Grocery", Name="Maize meal 5kg", Barcode="6001000000011", Price=D("59.99"), StoreId=far.StoreId
        )
    )
    db.session.commit()
    assert catalogue.matching("maize", HOME["lat"], HOME["lng"], 30)
    assert catalogue.matching("maize", -29.72, 31.07, 5) == []  # Umhlanga: too far


# ------------------------------------------------------------------------------------------------ admin account
def test_default_admin_is_created_on_first_sign_in_and_lands_on_the_admin_dashboard(client, app):
    app.config.update(DEFAULT_ADMIN_EMAIL="siya1@gmail.com", DEFAULT_ADMIN_PASSWORD="Siyabonga@2")
    wrong = client.post("/login", data={"Email": "siya1@gmail.com", "Password": "nope"})
    assert wrong.status_code == 401 and db.session.scalar(select(User)) is None

    response = client.post("/login", data={"Email": "siya1@gmail.com", "Password": "Siyabonga@2"})
    assert response.status_code == 302 and response.headers["Location"].endswith("/admin/")
    user = db.session.scalar(select(User).where(User.Email == "siya1@gmail.com"))
    assert user.is_admin and user.check_password("Siyabonga@2")
    assert client.get("/admin/").status_code == 200


def test_seed_admin_command(app):
    app.config.update(DEFAULT_ADMIN_EMAIL="siya1@gmail.com", DEFAULT_ADMIN_PASSWORD="Siyabonga@2")
    result = app.test_cli_runner().invoke(args=["seed-admin"])
    assert result.exit_code == 0 and "Created" in result.output
    user = db.session.scalar(select(User).where(User.Email == "siya1@gmail.com"))
    assert user.IsAdmin and user.check_password("Siyabonga@2")
    again = app.test_cli_runner().invoke(args=["seed-admin", "--keep-password"])
    assert again.exit_code == 0 and "Updated" in again.output


def test_admin_adds_a_user(boss):
    response = boss.post(
        "/admin/users/new",
        data={
            "FirstName": "Lindi",
            "LastName": "Zulu",
            "Email": "Lindi@Example.com",
            "Password": "Str0ng!Pass",
            "Role": "Student",
        },
    )
    assert response.status_code == 302
    user = db.session.scalar(select(User).where(User.Email == "lindi@example.com"))
    assert user and not user.IsAdmin and user.check_password("Str0ng!Pass")
    duplicate = boss.post(
        "/admin/users/new",
        data={
            "FirstName": "Lindi",
            "LastName": "Zulu",
            "Email": "lindi@example.com",
            "Password": "Str0ng!Pass",
            "Role": "Student",
        },
    )
    assert duplicate.status_code == 400 and "already exists" in duplicate.get_data(as_text=True)


# ------------------------------------------------------------------------------------------------ history maps
@pytest.fixture
def finished_trip(student, stores):
    provider = get_retail_provider()
    budgets.create_budget(student.UserId, "September shop", [("Grocery", D("900"))])
    for barcode in ("2000000000275", "2000000000305"):
        offers = [o for o in provider.get_offers_by_barcode(barcode, HOME["lat"], HOME["lng"], 15) if o.in_stock]
        shopping.add_product(student, sorted(offers, key=lambda o: o.store_name)[-1])
    shopping_list = budgets.get_active_list(budgets.get_active_budget(student.UserId))
    budgets.complete_purchase(student.UserId)
    shopping_list.DateClosed = utcnow() - timedelta(days=1)
    db.session.commit()
    return shopping_list


def test_every_trip_in_history_has_a_map_from_home(client, student, finished_trip):
    sign_in(client, student)
    html = client.get("/history").get_data(as_text=True)
    assert html.count("data-trip-map=") == 1
    assert "Home" in html and "1." in html


def test_trip_page_shows_the_route_from_the_residence(client, student, finished_trip, app):
    app.config["OSRM_BASE_URL"] = "http://127.0.0.1:9"  # unreachable: the route falls back to an estimate
    sign_in(client, student)
    html = client.get(f"/history/{finished_trip.ShoppingListId}").get_data(as_text=True)
    assert 'id="route-map"' in html and "Your residence" in html and "route.js" in html
    items = db.session.scalars(select(ListItem).where(ListItem.ShoppingListId == finished_trip.ShoppingListId)).all()
    for item in items:
        assert re.search(re.escape(item.ItemName), html)

    route = client.get(f"/history/{finished_trip.ShoppingListId}/route").get_json()
    assert route["home"] == HOME and route["stops"] and route["return_home"] is True
    assert {s["name"] for s in route["stops"]} == {i.StoreName for i in items}


def test_trip_route_is_private(client, make_user, finished_trip):
    sign_in(client, make_user())
    assert client.get(f"/history/{finished_trip.ShoppingListId}/route").status_code == 404
