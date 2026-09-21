"""The JSON API (/api/...): who may call it, what it validates, the shape of what it returns and how it fails.

Uses pytest-flask (``url_for`` inside tests, the app/client fixtures), factory_boy (tests/factories.py) and the offline
MockProvider that the testing config selects, so nothing here touches the network except where ``responses`` mocks it.
"""

import json
from decimal import Decimal

import pytest
import responses
from flask import url_for

from app.extensions import db
from app.services.retail_api import MockProvider, get_retail_provider
from tests.factories import BudgetFactory, ListItemFactory, StoreFactory, UserFactory

API_ENDPOINT_PREFIXES = ("api.", "shop_api.", "notifications_api.")
PUBLIC = {"api.geocode_address", "api.reverse_geocode_point"}  # the registration page uses these before sign-up


@pytest.fixture
def student():
    return UserFactory(password="Str0ng!Pass")


@pytest.fixture
def budget(student):
    return BudgetFactory(user=student)


@pytest.fixture
def api(app, student, login):
    """A client signed in as ``student``."""
    return login(student)


def api_rules(app):
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith(API_ENDPOINT_PREFIXES) and rule.endpoint not in PUBLIC:
            path = rule.rule
            for argument in rule.arguments:
                path = path.replace(f"<{argument}>", "x")
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                yield method, path


def test_the_provider_behind_these_tests_is_the_offline_mock(app):
    assert isinstance(get_retail_provider().inner, MockProvider)


# ------------------------------------------------------------------------------------------------------- access
def test_every_signed_in_route_answers_401_json_to_visitors(app, client):
    rules = list(api_rules(app))
    assert len(rules) >= 15
    for method, path in rules:
        response = client.open(path, method=method)
        assert response.status_code == 401, (method, path)
        assert response.get_json() == {"error": "Please sign in to continue."}, (method, path)


def test_the_geocoding_routes_are_public(client):
    assert client.get("/api/geocode?q=ab").status_code == 400  # reached the handler, not the sign-in wall
    assert client.get("/api/reverse-geocode").status_code == 400


def test_the_api_never_redirects_to_the_login_page(client):
    response = client.get(url_for("shop_api.get_list"))
    assert response.status_code == 401 and "Location" not in response.headers


def test_a_deactivated_student_is_locked_out_of_the_api(app, student, login):
    client = login(student)
    assert client.get(url_for("shop_api.get_list")).status_code in (200, 409)
    student.IsActive = False
    db.session.commit()
    assert app.test_client().get("/api/list").status_code == 401


# ------------------------------------------------------------------------------------------------------- search
def test_search_returns_priced_cards(api, budget):
    body = api.get(url_for("shop_api.search_products", q="milk")).get_json()
    assert {"results", "total", "page", "pages", "center", "radius_km", "query"} <= set(body)
    assert body["query"] == "milk" and body["total"] >= 5
    card = body["results"][0]
    assert {"name", "barcode", "price", "store_name", "store_id", "distance_km", "in_stock", "badges", "key"} <= set(
        card
    )
    assert Decimal(card["price"]) > 0 and card["price"].count(".") == 1  # exact ZAR string, never a float


def test_search_results_are_sorted_by_the_requested_order(api, budget):
    prices = [Decimal(c["price"]) for c in api.get("/api/search?q=milk&sort=lowest").get_json()["results"]]
    assert prices == sorted(prices)
    dearest = [Decimal(c["price"]) for c in api.get("/api/search?q=milk&sort=highest").get_json()["results"]]
    assert dearest == sorted(dearest, reverse=True)


def test_the_same_barcode_is_grouped_and_its_cheapest_offer_badged(api, budget):
    cards = api.get("/api/search?q=full cream milk").get_json()["results"]
    by_barcode = {}
    for card in cards:
        by_barcode.setdefault(card["barcode"], []).append(card)
    multi = [group for group in by_barcode.values() if len(group) > 1]
    assert multi, "the mock catalogue sells milk at several stores"
    for group in multi:
        lowest = min(Decimal(c["price"]) for c in group if c["in_stock"])
        badged = [Decimal(c["price"]) for c in group if "CHEAPEST" in c["badges"]]
        assert badged and set(badged) == {lowest}  # two stores at the same price both carry the badge


@pytest.mark.parametrize(
    "query, field",
    [
        ("q=milk&sort=bogus", "sort"),
        ("q=milk&min_price=abc", "min_price"),
        ("q=milk&min_price=50&max_price=10", "max_price"),
        ("q=milk&radius=-1", "radius"),
        ("q=milk&page=abc", "page"),
        ("q=" + "x" * 300, "q"),
    ],
)
def test_search_validates_its_parameters(api, budget, query, field):
    response = api.get("/api/search?" + query)
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] and field in body["fields"]


def test_search_finds_nothing_for_nonsense_without_failing(api, budget):
    body = api.get("/api/search?q=zzzqqxx").get_json()
    assert body["results"] == [] and body["total"] == 0


def test_product_details_lists_every_store_cheapest_first(api, budget):
    card = api.get("/api/search?q=milk").get_json()["results"][0]
    body = api.get(url_for("shop_api.product_details", barcode=card["barcode"], store_id=card["store_id"])).get_json()
    assert body["product"]["barcode"] == card["barcode"] and len(body["offers"]) >= 1
    in_stock = [Decimal(o["price"]) for o in body["offers"] if o["in_stock"]]
    assert in_stock == sorted(in_stock)
    assert api.get("/api/product").status_code == 400
    assert api.get("/api/product?barcode=0000000000000").status_code == 404


# ------------------------------------------------------------------------------------------------ the shopping list
def add(api, card, **extra):
    return api.post("/api/list/items", json={"barcode": card["barcode"], "store_id": card["store_id"], **extra})


def first_card(api, query="milk"):
    return api.get(f"/api/search?q={query}").get_json()["results"][0]


def test_adding_needs_a_budget_but_never_blocks_over_budget(api, student):
    card = first_card(api)
    without = add(api, card)
    assert without.status_code == 409 and without.get_json()["create_budget_url"] == "/budget/new"
    BudgetFactory(user=student, combined=20)  # R20: a single milk is fine, the second is over
    assert add(api, card).status_code == 201
    over = add(
        api,
        card,
    )
    assert over.status_code in (200, 201) and over.get_json()["quantity"] == 2  # over budget: a warning, not a block
    assert over.get_json()["over_budget"] is True


def test_adding_the_same_product_at_the_same_store_adds_one_to_the_quantity(api, budget):
    card = first_card(api)
    first, second = add(api, card), add(api, card)
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.get_json()["quantity"] == 2 and len(second.get_json()["items"]) == 1


def test_the_list_carries_the_budget_position_in_rand(api, budget):
    card = first_card(api)
    add(api, card)
    state = api.get(url_for("shop_api.get_list")).get_json()
    assert {"list", "items", "budget", "over_budget", "alert", "potential_savings"} <= set(state)
    assert Decimal(state["items"][0]["line_total"]) == Decimal(card["price"])
    assert Decimal(state["budget"]["used"]) == Decimal(card["price"])
    assert Decimal(state["budget"]["remaining"]) == Decimal("1100.00") - Decimal(card["price"])


def test_quantity_changes_and_removal(api, budget):
    added = add(api, first_card(api)).get_json()["added"]
    changed = api.patch(f"/api/list/items/{added['id']}", json={"quantity": 3}).get_json()
    assert changed["items"][0]["quantity"] == 3
    assert api.patch(f"/api/list/items/{added['id']}", json={"quantity": "abc"}).status_code == 400
    assert api.patch(f"/api/list/items/{added['id']}", json={"quantity": True}).status_code == 400
    assert api.delete(f"/api/list/items/{added['id']}").get_json()["items"] == []
    assert api.delete(f"/api/list/items/{added['id']}").status_code == 404


def test_one_student_cannot_touch_another_students_items(app, api, budget):
    item = ListItemFactory(budget=budget)
    stranger = UserFactory()
    BudgetFactory(user=stranger)
    other = app.test_client()
    with other.session_transaction() as session:
        session["_user_id"], session["_fresh"] = stranger.get_id(), True
    assert other.patch(f"/api/list/items/{item.ListItemId}", json={"quantity": 9}).status_code == 404
    assert other.delete(f"/api/list/items/{item.ListItemId}").status_code == 404
    assert api.patch(f"/api/list/items/{item.ListItemId}", json={"quantity": 2}).status_code == 200


def test_bad_add_bodies(api, budget):
    assert api.post("/api/list/items", json={}).status_code == 400
    assert api.post("/api/list/items", data="not json", content_type="text/plain").status_code == 400
    assert api.post("/api/list/items", json=["a", "list"]).status_code == 400
    assert (
        api.post("/api/list/items", json={"barcode": 123, "store_id": "x"}).status_code == 400
    )  # a number is no barcode
    assert api.post("/api/list/items", json={"barcode": "0000000000000", "store_id": "nowhere"}).status_code == 404


def test_the_price_comes_from_the_server_not_the_browser(api, budget):
    card = first_card(api)
    added = add(api, card, price="0.01", unit_cost="0.01").get_json()["added"]
    assert added["unit_cost"] == card["price"]


def test_rename_the_list(api, budget):
    assert api.patch("/api/list", json={"title": "Sunday shop"}).get_json()["list"]["title"] == "Sunday shop"
    assert api.patch("/api/list", json={"title": "  "}).status_code == 400


# ------------------------------------------------------------------------------------------- stores, route, savings
def test_stores_near_lists_the_nearest_first(api, budget):
    near = StoreFactory(Name="Checkers Near", Latitude=-29.8590, Longitude=31.0220)
    far = StoreFactory(Name="Checkers Far", Latitude=-29.7300, Longitude=31.0700)
    stores = api.get(url_for("api.stores_near_point", radius=30)).get_json()["stores"]
    names = [s["name"] for s in stores]
    assert names.index(near.Name) < names.index(far.Name) and stores[0]["distance_km"] <= stores[-1]["distance_km"]
    assert api.get("/api/stores/near?lat=abc&lng=1").status_code == 400
    assert api.get("/api/stores/near?lat=1").status_code == 400
    assert api.get("/api/stores/near?lat=95&lng=31").status_code == 400


def test_the_route_needs_items_and_then_groups_stops_by_store(api, budget):
    assert api.get(url_for("shop_api.shopping_route")).status_code == 409
    add(api, first_card(api))
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mocked:
        mocked.add(
            responses.GET, url=__import__("re").compile(r".*/route/v1/.*"), status=503
        )  # OSRM down: straight-line estimate
        body = api.get("/api/route").get_json()
    assert body["stops"] and body["distance_km"] > 0 and body["source"] == "estimate"
    assert [stop["order"] for stop in body["stops"]] == list(range(1, len(body["stops"]) + 1))


def test_savings_and_recommendations_answer_for_a_new_student(api, budget):
    assert api.get("/api/savings").status_code == 200
    body = api.get("/api/recommendations?limit=4").get_json()
    assert isinstance(body["results"], list) and len(body["results"]) <= 4
    assert api.get("/api/recommendations?limit=abc").status_code == 400


# ------------------------------------------------------------------------------------------------------ geocoding
@responses.activate
def test_geocode_maps_nominatim_results(client, app):
    responses.add(
        responses.GET,
        f"{app.config['NOMINATIM_BASE_URL']}/search",
        json=[{"display_name": "Berea, Durban, KwaZulu-Natal, South Africa", "lat": "-29.8500", "lon": "31.0200"}],
    )
    body = client.get("/api/geocode?q=Berea Durban").get_json()
    assert body["results"][0]["lat"] == pytest.approx(-29.85) and "Berea" in body["results"][0]["display_name"]


@responses.activate
def test_geocode_failure_is_a_502_json(client, app):
    responses.add(responses.GET, f"{app.config['NOMINATIM_BASE_URL']}/search", status=500)
    response = client.get("/api/geocode?q=Berea Durban")
    assert response.status_code == 502 and response.get_json() == {"error": "Address lookup is unavailable right now."}


@pytest.mark.parametrize("query", ["", "q=ab", "q=" + "x" * 201])
def test_geocode_validates_length(client, query):
    assert client.get("/api/geocode?" + query).status_code == 400


@pytest.mark.parametrize("query", ["lat=abc&lng=1", "lat=95&lng=1", "lat=1&lng=190", "lat=nan&lng=1"])
def test_reverse_geocode_validates_coordinates(client, query):
    assert client.get("/api/reverse-geocode?" + query).status_code == 400


# ------------------------------------------------------------------------------------------------------- weather
def weather_payload():
    return {
        "current": {"temperature_2m": 24.6, "relative_humidity_2m": 68, "weather_code": 2, "is_day": 1},
        "daily": {"temperature_2m_max": [27.2], "temperature_2m_min": [16.8]},
    }


@responses.activate
def test_weather_for_the_saved_address(api, app, student):
    responses.add(responses.GET, f"{app.config['OPEN_METEO_BASE_URL'].rstrip('/')}/forecast", json=weather_payload())
    body = api.get(url_for("shop_api.weather")).get_json()
    assert body["place"] == "Your area" and body["temperature_c"] == 25 and body["humidity"] == 68


def test_weather_validates_its_parameters(api):
    assert api.get("/api/weather?lat=1").status_code == 400
    assert api.get("/api/weather?lat=abc&lng=1").status_code == 400
    assert api.get("/api/weather?lat=99&lng=1").status_code == 400


@responses.activate
def test_a_weather_outage_is_a_502_json(api, app):
    responses.add(responses.GET, f"{app.config['OPEN_METEO_BASE_URL'].rstrip('/')}/forecast", status=500)
    response = api.get("/api/weather")
    assert response.status_code == 502 and "not available" in response.get_json()["error"]


# ------------------------------------------------------------------------------------------------ provider failures
def test_a_retail_outage_is_a_502_and_leaks_nothing(api, budget, monkeypatch):
    from app.services import search
    from app.services.retail_api import RetailAPIError

    def boom(*args, **kwargs):
        raise RetailAPIError("Apify rejected the API token apify_SECRET_TOKEN", service="apify", status=401)

    monkeypatch.setattr(search, "run_search", boom)
    response = api.get("/api/search?q=milk")
    assert response.status_code == 502
    assert "SECRET" not in response.get_data(as_text=True) and "try again" in response.get_json()["error"]


def test_a_provider_that_is_not_configured_is_a_503(api, budget, monkeypatch):
    from app.services import search
    from app.services.retail_api import RetailConfigError

    monkeypatch.setattr(
        search, "run_search", lambda *a, **k: (_ for _ in ()).throw(RetailConfigError("APIFY_API_TOKEN is not set."))
    )
    response = api.get("/api/search?q=milk")
    assert response.status_code == 503 and "APIFY" not in response.get_data(as_text=True)


# ---------------------------------------------------------------------------------------------- notifications API
def test_notifications_api_shape(api, budget):
    body = api.get(url_for("notifications_api.list_notifications")).get_json()
    assert set(body) >= {"notifications", "unread"} and body["unread"] == 0
    assert api.post("/api/notifications/read-all").get_json()["unread"] == 0
    assert api.post("/api/notifications/NTF-doesnotexist/read").status_code == 404


def test_json_bodies_are_valid_json_utf8(api, budget):
    response = api.get("/api/search?q=milk")
    assert response.mimetype == "application/json"
    json.loads(response.get_data(as_text=True))
