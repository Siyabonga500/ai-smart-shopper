"""Ticking items off as purchased on the shopping list, and the "used / saved" message after a purchase."""

import re
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import ListItem
from app.services import budgets, shopping
from app.services.retail_api import get_retail_provider

D = Decimal
HOME = {"lat": -29.8587, "lng": 31.0218}
MILK, CHOC_MILK = "2000000000275", "2000000000305"


@pytest.fixture
def user(make_user):
    return make_user(Latitude=HOME["lat"], Longitude=HOME["lng"])


@pytest.fixture
def signed_in(client, user, login):
    login(user)
    return client


def add(user, barcode):
    offers = [
        o for o in get_retail_provider().get_offers_by_barcode(barcode, HOME["lat"], HOME["lng"], 15) if o.in_stock
    ]
    return shopping.add_product(user, min(offers, key=lambda o: (o.price, o.store_name))).item


@pytest.fixture
def items(user):
    budgets.create_budget(user.UserId, "September", [("Grocery", D("500"))])
    return [add(user, MILK), add(user, CHOC_MILK)]


def test_each_item_has_a_purchased_button_and_the_list_counts_them(signed_in, items):
    html = signed_in.get("/list").get_data(as_text=True)
    assert html.count('data-action="purchased"') == 2 and "0 of 2 items purchased" in html

    response = signed_in.post(f"/api/list/items/{items[0].ListItemId}/purchased", json={"purchased": True})
    assert response.status_code == 200 and response.get_json()["purchased"] is True
    assert db.session.get(ListItem, items[0].ListItemId).IsCollected is True
    assert db.session.get(ListItem, items[0].ListItemId).IsPurchased is False  # the trip is not closed yet

    html = signed_in.get("/list/fragment").get_data(as_text=True)
    assert (
        "1 of 2 items purchased" in html
        and "is-collected" in html
        and ">Purchased" in html.replace("\n", "").replace("  ", "")
    )

    signed_in.post(f"/api/list/items/{items[0].ListItemId}/purchased", json={"purchased": False})  # undo
    assert db.session.get(ListItem, items[0].ListItemId).IsCollected is False


def test_summary_and_done_are_blocked_until_every_item_is_purchased_or_deleted(signed_in, user, items):
    html = signed_in.get("/list").get_data(as_text=True)
    assert 'href="/list/summary"' not in html and "disabled" in html.split("Proceed to Summary")[0][-200:]
    assert "2 items are not marked as purchased" in html

    signed_in.post(f"/api/list/items/{items[0].ListItemId}/purchased", json={"purchased": True})
    html = signed_in.get("/list/fragment").get_data(as_text=True)
    assert 'href="/list/summary"' not in html and "1 item is not marked as purchased: " + items[1].ItemName in html
    assert signed_in.get("/list/summary").headers["Location"].endswith("/list")  # typing the address does not help
    done = signed_in.post("/list/complete")
    assert done.headers["Location"].endswith("/list") and budgets.get_active_budget(user.UserId) is not None

    signed_in.delete(f"/api/list/items/{items[1].ListItemId}")  # deleting the other one also unblocks
    html = signed_in.get("/list/fragment").get_data(as_text=True)
    assert 'href="/list/summary"' in html
    assert "Every item is marked as purchased" in signed_in.get("/list/summary").get_data(as_text=True)


def test_purchased_needs_a_true_or_false_and_your_own_item(signed_in, items, make_user, client, login):
    assert (
        signed_in.post(f"/api/list/items/{items[0].ListItemId}/purchased", json={"purchased": "yes"}).status_code == 400
    )
    other = make_user()
    login(other)
    assert client.post(f"/api/list/items/{items[0].ListItemId}/purchased", json={"purchased": True}).status_code in (
        404,
        409,
    )


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", "", html).replace("\u00a0", " ").split())


def test_done_says_how_much_was_used_and_saved_and_history_shows_it(signed_in, user, items):
    shopping_list = budgets.get_active_list(budgets.get_active_budget(user.UserId))
    spent = budgets.position(budgets.get_active_budget(user.UserId)).in_list
    saved = D("500.00") - spent
    assert saved > 0
    for item in items:
        shopping.set_collected(user, item.ListItemId, True)

    text = _text(signed_in.post("/list/complete", follow_redirects=True).get_data(as_text=True))
    assert "You budgeted R500 and used R" in text and "You saved R" in text and "from your budget" in text

    assert "Saved R" in _text(signed_in.get("/history").get_data(as_text=True))
    detail = _text(signed_in.get(f"/history/{shopping_list.ShoppingListId}").get_data(as_text=True))
    assert "Budgeted" in detail and "You saved R" in detail
