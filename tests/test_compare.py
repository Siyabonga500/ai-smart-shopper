"""The Compare window (price against distance from the student) and the DUT residence drop-down on profile edit."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.data.dut_residences import get_residence
from app.extensions import db
from app.models import CatalogueProduct, Store
from app.services import admin_stores, products
from app.services.retail_api import Product

D = Decimal
HOME = {"lat": -29.8587, "lng": 31.0218}


def milk(store, price, km, in_stock=True):
    return Product(
        name="Clover Milk 2L",
        barcode="6001299001234",
        price=D(price),
        image_url=None,
        store_name=store,
        store_address=None,
        store_lat=-29.8,
        store_lng=31.0,
        category="Grocery",
        in_stock=in_stock,
        retailer=store.split()[0],
        store_id=store.lower().replace(" ", "-"),
        distance_km=km,
    )


def test_the_cheapest_store_is_flagged_when_a_dearer_one_is_much_closer():
    offers = [milk("Checkers Gateway", "30.00", 8.0), milk("Shoprite West Street", "31.00", 2.0)]
    result = products.distance_comparison(offers)
    assert result["far"] is True
    assert result["cheapest_key"] == "6001299001234@checkers-gateway"
    assert result["nearest_key"] == "6001299001234@shoprite-west-street"
    assert result["text"] == (
        "Cheapest: R30 at Checkers Gateway, 8 km away from you. "
        "Shoprite West Street is R1 more but only 2 km away (6 km closer)."
    )


def test_no_flag_when_the_cheapest_is_also_the_closest_or_the_others_are_out_of_stock():
    close = products.distance_comparison([milk("Checkers A", "30.00", 1.5), milk("Shoprite B", "31.00", 2.0)])
    assert close["far"] is False and "also the closest" in close["text"]
    gone = products.distance_comparison([milk("Checkers A", "30.00", 8.0), milk("Shoprite B", "31.00", 2.0, False)])
    assert gone["far"] is False
    barely = products.distance_comparison([milk("Checkers A", "30.00", 2.3), milk("Shoprite B", "31.00", 2.0)])
    assert barely["far"] is False  # 300 m is not worth a warning


@pytest.fixture
def stores(app):
    admin_stores.add_known_stores()
    db.session.commit()
    return {s.Slug: s for s in db.session.scalars(select(Store))}


def test_compare_api_marks_the_far_cheapest_store_in_red_and_names_the_closer_one(client, login, make_user, stores):
    student = make_user(Latitude=HOME["lat"], Longitude=HOME["lng"])
    far, near = stores["checkers-musgrave"], stores["shoprite-durban-west-street"]  # about 2.1 km and 0.3 km away
    for store, price in ((far, "30.00"), (near, "31.00")):
        db.session.add(
            CatalogueProduct(
                Category="Grocery", Name="Test Milk", Barcode="6009999999991", Price=D(price), StoreId=store.StoreId
            )
        )
    db.session.commit()
    login(student)
    data = client.get("/api/product?barcode=6009999999991").get_json()
    by_store = {o["store_name"]: o for o in data["offers"]}
    cheap, close = by_store[far.Name], by_store[near.Name]
    assert cheap["far"] is True and "CHEAPEST" in cheap["badges"]
    assert close["far"] is False and "CLOSEST" in close["badges"]
    assert close["more_than_cheapest"] == "1.00" and close["closer_than_cheapest_km"] > 1.5
    assert data["comparison"]["far"] is True and "away from you" in data["comparison"]["text"]
    assert data["home_known"] is True


def test_search_cards_offer_compare_instead_of_view_and_add():
    source = open("app/static/js/search.js", encoding="utf-8").read()
    card = source[source.index("function cardEl") : source.index("function skeletons")]
    assert '"Compare"' in card and '"View"' not in card and "addButton(" not in card


# ------------------------------------------------------------------------------------------------ profile residence
def test_profile_edit_shows_the_dut_residences_and_the_map(client, login, make_user):
    residence = get_residence("berea")
    user = make_user(ResidentialAddress=residence.full_address)
    login(user)
    html = client.get("/profile/edit").get_data(as_text=True)
    assert 'id="Residence"' in html and "Berea Residence" in html and "data-map-picker" in html
    assert (
        'value="berea" data-address' in html
        and 'value="berea" data-address="%s" data-lat' % residence.full_address in html
    )
    assert "selected" in html[html.index('value="berea"') : html.index('value="berea"') + 300]
    assert "residence_picker.js" in html


def test_choosing_a_residence_fills_an_empty_address(client, login, make_user):
    user = make_user(CellphoneNumber="0821234567")
    login(user)
    response = client.post(
        "/profile/edit",
        data={
            "FirstName": "Thabo",
            "LastName": "Mokoena",
            "CellphoneNumber": "0821234567",
            "Residence": "berea",
            "ResidentialAddress": "",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    db.session.refresh(user)
    assert user.ResidentialAddress == get_residence("berea").full_address


def test_the_tip_names_the_cheapest_of_the_closer_stores_not_just_the_closest():
    offers = [milk("Shoprite A", "13.99", 2.2), milk("Checkers B", "14.99", 0.7), milk("Spar C", "15.99", 0.4)]
    result = products.distance_comparison(offers)
    assert result["far"] and "Checkers B is R1 more but only 0.7 km away (1.5 km closer)" in result["text"]
    assert result["nearest_key"].endswith("@spar-c")  # still the CLOSEST badge
