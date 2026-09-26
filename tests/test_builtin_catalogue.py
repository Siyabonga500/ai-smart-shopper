"""Admin > Built-in products: edit, delete, restore and undo changes to the built-in (mock) catalogue."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, MockProductOverride, MockRetailerOverride
from app.services import builtin_catalogue as builtin
from app.services.retail_api import get_retail_provider

D = Decimal
HOME = {"lat": -29.8587, "lng": 31.0218}
MILK = "2000000000275"


def offers(barcode=MILK):
    return get_retail_provider().get_offers_by_barcode(barcode, HOME["lat"], HOME["lng"], 60)


@pytest.fixture
def boss(client, make_user, login):
    login(make_user(email="boss@example.com", IsAdmin=True))
    return client


def form_for(barcode=MILK, **extra):
    product = builtin.get(barcode)
    data = {
        "name": product.item.name,
        "brand": product.item.brand,
        "category": product.item.category,
        "price": str(product.item.price),
        "pictures": "",
    }
    for c in product.chains:
        key = c.retailer.replace(" ", "_")
        if c.listed:
            data[f"listed_{key}"] = "1"
        if c.in_stock:
            data[f"stock_{key}"] = "1"
    data.update(extra)
    return {k: v for k, v in data.items() if v is not None}


def test_the_list_shows_every_built_in_product(boss):
    html = boss.get("/admin/catalogue?q=clover").get_data(as_text=True)
    assert "Built-in products" in html and "Clover" in html and "Edit" in html and "Delete" in html


def test_edit_name_price_and_one_chains_price_and_stock(boss):
    before = {o.retailer: o for o in offers()}
    assert "Checkers" in before
    response = boss.post(
        f"/admin/catalogue/{MILK}",
        data=form_for(
            name="Clover Full Cream Milk 2L (Special)", price="40.00", price_Checkers="19.99", stock_Shoprite=None
        ),
    )
    assert response.status_code == 302
    after = {o.retailer: o for o in offers()}
    assert after["Checkers"].price == D("19.99")  # the chain price set by the admin
    assert all(o.name == "Clover Full Cream Milk 2L (Special)" for o in after.values())
    if "Shoprite" in after:
        assert after["Shoprite"].in_stock is False
    assert after["Pick n Pay"].price != before["Pick n Pay"].price  # follows the new reference price
    assert db.session.scalar(select(AuditLog).where(AuditLog.Action == "builtin.update")) is not None

    page = boss.get(f"/admin/catalogue/{MILK}").get_data(as_text=True)
    assert "Edited" in page and 'value="19.99"' in page


def test_a_chain_can_stop_stocking_it(boss):
    assert "Checkers" in {o.retailer for o in offers()}
    boss.post(f"/admin/catalogue/{MILK}", data=form_for(listed_Checkers=None))
    assert "Checkers" not in {o.retailer for o in offers()}


def test_delete_restore_and_undo(boss, client):
    boss.post(f"/admin/catalogue/{MILK}/delete")
    assert offers() == []
    assert all(c["barcode"] != MILK for c in client.get("/api/search?q=clover").get_json()["results"])
    assert "Deleted" in boss.get("/admin/catalogue?status=deleted").get_data(as_text=True)

    boss.post(f"/admin/catalogue/{MILK}/restore")
    assert offers()
    boss.post(f"/admin/catalogue/{MILK}", data=form_for(price="99"))
    boss.post(f"/admin/catalogue/{MILK}/reset")
    assert db.session.get(MockProductOverride, MILK) is None
    assert db.session.query(MockRetailerOverride).count() == 0
    actions = set(db.session.scalars(select(AuditLog.Action)))
    assert {"builtin.delete", "builtin.restore", "builtin.reset"} <= actions


def test_bad_input_is_refused(boss):
    response = boss.post(f"/admin/catalogue/{MILK}", data=form_for(price="abc"))
    assert response.status_code == 400 and "reference price" in response.get_data(as_text=True)
    response = boss.post(f"/admin/catalogue/{MILK}", data=form_for(price_Checkers="-5"))
    assert response.status_code == 400
    response = boss.post(f"/admin/catalogue/{MILK}", data=form_for(pictures="ftp://nope"))
    assert response.status_code == 400
    assert db.session.get(MockProductOverride, MILK) is None  # nothing saved


def test_pictures_can_be_set_while_editing(boss):
    boss.post(
        f"/admin/catalogue/{MILK}",
        data=form_for(pictures="https://img.example.com/a.jpg\nhttps://img.example.com/b.jpg"),
    )
    assert all(len(o.images) == 2 for o in offers())


def test_students_cannot_edit(client, make_user, login):
    login(make_user())
    assert client.post(f"/admin/catalogue/{MILK}/delete").status_code == 403
