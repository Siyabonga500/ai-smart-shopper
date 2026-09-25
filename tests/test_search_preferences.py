"""Saved preferences used on products in search, the "All" browse, the 0-60 km distance filter, and suggestions."""

from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Preference
from app.services.recommendations import PreferenceProfile, preference_reasons, preference_suggestions
from app.services.retail_api import Product

HOME = {"lat": -29.8587, "lng": 31.0218}


def product(**extra):
    base = dict(name="Clover Milk 2L", barcode="1", price=Decimal("30"), image_url=None, store_name="Checkers Gateway",
                store_address=None, store_lat=None, store_lng=None, category="Grocery", brand="Clover", retailer="Checkers")
    base.update(extra)
    return Product(**base)


def test_preference_reasons_cover_store_brand_category_and_diet():
    profile = PreferenceProfile(frozenset({"checkers"}), frozenset({"clover"}), frozenset({"grocery"}), frozenset({"sugar free"}))
    reasons = preference_reasons(product(name="Clover Milk Sugar Free 1L"), profile)
    assert reasons == ["Your store: Checkers", "Your brand: Clover", "Your category: Grocery", "Dietary: Sugar free"]
    assert preference_reasons(product(retailer="Shoprite", store_name="Shoprite X", brand="Other"), PreferenceProfile(
        frozenset({"checkers"}), frozenset(), frozenset(), frozenset())) == []
    clothing = PreferenceProfile(frozenset(), frozenset(), frozenset({"clothing"}))
    assert preference_reasons(product(category="Clothes"), clothing) == ["Your category: Clothes"]


@pytest.fixture
def signed_in(client, make_user, login):
    user = make_user(Latitude=HOME["lat"], Longitude=HOME["lng"])
    login(user)
    client.user = user
    return client


def test_search_puts_preferred_products_first_when_the_student_has_preferences(signed_in):
    plain = signed_in.get("/api/search?q=milk").get_json()
    assert plain["sort"] == "lowest" and not any(c["preferred"] for c in plain["results"])

    db.session.add(Preference(UserId=signed_in.user.UserId, PreferenceType="Store", PreferenceValue="Woolworths"))
    db.session.commit()
    data = signed_in.get("/api/search?q=milk").get_json()
    assert data["sort"] == "preferred"
    flags = [bool(c["preferred"]) for c in data["results"]]
    assert flags[0] and flags == sorted(flags, reverse=True)  # every match comes before the rest
    assert any("Your store: Woolworths" in c["preferred"] for c in data["results"])
    cheapest_first = signed_in.get("/api/search?q=milk&sort=lowest").get_json()
    assert cheapest_first["sort"] == "lowest"


def test_all_shows_every_product_and_the_distance_filter_goes_from_0_to_60_km(signed_in):
    everything = signed_in.get("/api/search").get_json()
    assert everything["total"] > 100 and everything["radius_km"] == 60
    assert signed_in.get("/api/search?radius=0").get_json()["total"] == 0
    assert signed_in.get("/api/search?radius=2").get_json()["total"] < everything["total"]
    assert signed_in.get("/api/search?radius=500").get_json()["radius_km"] == 60


def test_preference_suggestions_follow_the_type(app, signed_in):
    suggestions = preference_suggestions()
    assert {"Checkers", "Pick n Pay", "Mr Price"} <= set(suggestions["Store"])
    assert "Clover" in suggestions["Brand"] and "Vegetarian" in suggestions["Dietary"]
    assert suggestions["Category"] == ["Grocery", "Toiletries", "Clothing"]
    html = signed_in.get("/profile").get_data(as_text=True)
    assert 'list="pref-suggestions"' in html and "pref-suggestions-data" in html and "preferences.js" in html
