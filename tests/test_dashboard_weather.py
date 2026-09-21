"""Step 8: the dashboard page, its "recently bought" rule, potential savings and the Open-Meteo weather card."""

from datetime import timedelta
from decimal import Decimal

import pytest
import requests
import responses

from app.extensions import cache, db, search_limiter
from app.models import ListItem
from app.services import budgets, dashboard, shopping, weather
from app.services.retail_api import get_retail_provider
from app.utils.dates import utcnow

D = Decimal
MILK = "2000000000275"
HOME = {"lat": -29.8587, "lng": 31.0218}


# ------------------------------------------------------------------------------------------------ fixtures
@pytest.fixture
def user(make_user):
    return make_user(
        Latitude=HOME["lat"],
        Longitude=HOME["lng"],
        ResidentialAddress="79 Mansfield Road, Berea, Durban, 4001, South Africa",
    )


@pytest.fixture
def signed_in(client, user, login):
    login(user)
    return client


@pytest.fixture
def provider(app):
    return get_retail_provider()


@pytest.fixture
def fast_retries(monkeypatch):
    """Do not really wait between retries of the weather call."""
    monkeypatch.setattr("app.services.http._retry_delay", lambda *a, **k: 0)


@pytest.fixture(autouse=True)
def _fresh_cache(app):
    cache.clear()
    yield
    cache.clear()


def forecast_url(app):
    return f"{app.config['OPEN_METEO_BASE_URL'].rstrip('/')}/forecast"


def forecast(code=2, temp=24.6, humidity=68, high=27.2, low=16.8, is_day=1):
    return {
        "current": {"temperature_2m": temp, "relative_humidity_2m": humidity, "weather_code": code, "is_day": is_day},
        "daily": {"temperature_2m_max": [high], "temperature_2m_min": [low]},
    }


def bought(user, name, unit, qty=1, days_ago=1, store="Checkers", closed_list=None):
    """A purchased item on a closed list, bought ``days_ago`` days ago."""
    if closed_list is None:
        budget = budgets.create_budget(user.UserId, f"Old {name}", [("Grocery", D("5000"))])
        closed_list = budgets.get_active_list(budget)
        closed_list.IsActive = False
        budget.IsActive = False
    item = ListItem(
        UserId=user.UserId,
        ShoppingListId=closed_list.ShoppingListId,
        ItemName=name,
        UnitCost=D(unit),
        ItemQuantity=qty,
        StoreName=store,
        IsPurchased=True,
        PurchasedDate=utcnow() - timedelta(days=days_ago),
    )
    db.session.add(item)
    db.session.commit()
    return item


# ================================================================================================ weather service
@pytest.mark.parametrize(
    "code,words,icon",
    [
        (0, "Clear sky", "sun"),
        (2, "Partly cloudy", "cloud-sun"),
        (3, "Overcast", "cloud"),
        (45, "Fog", "cloud-fog"),
        (63, "Rain", "cloud-rain"),
        (82, "Heavy showers", "cloud-rain-heavy"),
        (95, "Thunderstorm", "cloud-lightning-rain"),
        (12345, "Unknown", "cloud"),
    ],
)
def test_wmo_codes_are_described_in_words(code, words, icon):
    assert weather.describe(code) == (words, icon)


def test_clear_and_partly_cloudy_nights_get_night_icons():
    assert weather.describe(0, is_day=False) == ("Clear sky", "moon-stars")
    assert weather.describe(2, is_day=False) == ("Partly cloudy", "cloud-moon")
    assert weather.describe(63, is_day=False)[1] == "cloud-rain"


def test_parse_rounds_and_maps():
    assert weather.parse(forecast()) == {
        "temperature_c": 25,
        "humidity": 68,
        "condition": "Partly cloudy",
        "icon": "cloud-sun",
        "code": 2,
        "is_day": True,
        "high_c": 27,
        "low_c": 17,
    }


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"current": {}, "daily": {}},
        {"current": forecast()["current"]},
        {"current": forecast()["current"], "daily": {"temperature_2m_max": [], "temperature_2m_min": []}},
        {"current": {**forecast()["current"], "temperature_2m": None}, "daily": forecast()["daily"]},
        {"current": {**forecast()["current"], "temperature_2m": "warm"}, "daily": forecast()["daily"]},
        {"current": {**forecast()["current"], "relative_humidity_2m": float("nan")}, "daily": forecast()["daily"]},
        {"current": {**forecast()["current"], "weather_code": True}, "daily": forecast()["daily"]},
        "not a dict",
        None,
    ],
)
def test_parse_refuses_incomplete_answers(bad):
    with pytest.raises(weather.WeatherError):
        weather.parse(bad)


@responses.activate
def test_get_weather_asks_open_meteo_once_and_caches_per_cell(app, real_cache):
    responses.get(forecast_url(app), json=forecast())
    first = weather.get_weather(-29.8587, 31.0218)
    second = weather.get_weather(-29.8601, 31.0199)  # same ~1 km cell (rounded to 2 decimals)
    assert first == second and first["temperature_c"] == 25
    assert len(responses.calls) == 1
    query = responses.calls[0].request.params
    assert query["latitude"] == "-29.8600" and query["longitude"] == "31.0200"
    assert query["timezone"] == "Africa/Johannesburg" and query["forecast_days"] == "1"
    assert "temperature_2m" in query["current"] and "temperature_2m_max" in query["daily"]
    weather.get_weather(-26.2, 28.0)  # Johannesburg is another cell
    assert len(responses.calls) == 2


@responses.activate
def test_failures_are_not_cached(app, real_cache, fast_retries):
    responses.get(forecast_url(app), status=500)
    with pytest.raises(weather.WeatherError):
        weather.get_weather(HOME["lat"], HOME["lng"])
    responses.replace(responses.GET, forecast_url(app), json=forecast())
    assert weather.get_weather(HOME["lat"], HOME["lng"])["condition"] == "Partly cloudy"


def test_the_cache_lifetime_is_thirty_minutes(app):
    assert app.config["WEATHER_CACHE_TTL"] == 30 * 60


@responses.activate
@pytest.mark.parametrize(
    "reply",
    [
        {"status": 500},
        {"status": 404},
        {"body": "<html>", "status": 200},
        {"json": {"error": True}, "status": 200},
        {"body": requests.ConnectionError("down")},
    ],
)
def test_service_trouble_becomes_weather_error(app, fast_retries, reply):
    responses.get(forecast_url(app), **reply)
    with pytest.raises(weather.WeatherError):
        weather.get_weather(HOME["lat"], HOME["lng"])


@pytest.mark.parametrize(
    "address,expected",
    [
        ("79 Mansfield Road, Berea, Durban, 4001, South Africa", "Durban"),
        ("12 Ridge Road, Musgrave, Durban", "Durban"),
        ("Steve Biko Campus, Berea, South Africa", "Berea"),
        ("Durban", "Fallback"),
        ("", "Fallback"),
        (None, "Fallback"),
        (" , ,", "Fallback"),
    ],
)
def test_place_label(address, expected):
    assert weather.place_label(address, "Fallback") == expected


# ================================================================================================ /api/weather
@responses.activate
def test_api_weather_uses_the_saved_address(app, signed_in):
    responses.get(forecast_url(app), json=forecast(code=63, temp=19.4))
    body = signed_in.get("/api/weather").get_json()
    assert (
        body["place"] == "Durban"
        and body["temperature_c"] == 19
        and body["condition"] == "Rain"
        and body["icon"] == "cloud-rain"
    )
    assert body["high_c"] == 27 and body["low_c"] == 17 and body["humidity"] == 68
    assert responses.calls[0].request.params["latitude"] == "-29.8600"


@responses.activate
def test_api_weather_without_a_saved_location_uses_durban(app, client, make_user, login):
    login(make_user())
    responses.get(forecast_url(app), json=forecast())
    body = client.get("/api/weather").get_json()
    assert body["place"] == "Durban"
    assert responses.calls[0].request.params["latitude"] == "-29.8600"  # Durban city centre


@responses.activate
def test_api_weather_at_a_live_location(app, signed_in):
    responses.get(forecast_url(app), json=forecast())
    body = signed_in.get("/api/weather?lat=-26.2041&lng=28.0473").get_json()
    assert body["place"] == "Your location"
    assert responses.calls[0].request.params["latitude"] == "-26.2000"


@pytest.mark.parametrize(
    "query",
    ["lat=-29.8", "lng=31.0", "lat=abc&lng=31", "lat=95&lng=31", "lat=-29&lng=181", "lat=nan&lng=31", "lat=1&lng=inf"],
)
@responses.activate
def test_api_weather_validates_the_point(signed_in, query):
    response = signed_in.get(f"/api/weather?{query}")
    assert response.status_code == 400 and response.get_json()["error"]
    assert len(responses.calls) == 0


@responses.activate
def test_api_weather_outage_is_a_502_with_a_message(app, signed_in, fast_retries):
    responses.get(forecast_url(app), status=503)
    response = signed_in.get("/api/weather")
    assert response.status_code == 502
    assert response.get_json() == {"error": "The weather is not available right now."}


@responses.activate
def test_api_weather_is_rate_limited_per_student(app, signed_in):
    app.config["RATELIMIT_ENABLED"] = True
    search_limiter.limit = 2
    search_limiter.clear()
    responses.get(forecast_url(app), json=forecast())
    try:
        assert [signed_in.get("/api/weather").status_code for _ in range(4)] == [200, 200, 429, 429]
    finally:
        search_limiter.clear()


def test_api_weather_requires_sign_in(client):
    response = client.get("/api/weather", headers={"Accept": "application/json"})
    assert response.status_code in (401, 302)


# ================================================================================================ recently bought
def test_high_cost_items_come_first_newest_first_capped_at_four(app, user):
    for index in range(6):
        bought(user, f"TV {index}", "899.00", days_ago=index + 1)
    bought(user, "Cheap thing", "20.00", days_ago=0)  # newest, but not a high-cost item
    names = [item.ItemName for item in dashboard.recently_bought(user.UserId)]
    assert names == ["TV 0", "TV 1", "TV 2", "TV 3"]


def test_the_threshold_is_r500_inclusive_and_uses_the_unit_cost(app, user):
    bought(user, "Exactly 500", "500.00", days_ago=5)
    bought(user, "Just under", "499.99", qty=3, days_ago=1)  # R1 499.97 for the line, but each costs under R500
    names = [item.ItemName for item in dashboard.recently_bought(user.UserId)]
    assert names == ["Exactly 500"]


def test_without_high_cost_items_the_four_most_expensive_purchases_of_the_last_60_days(app, user):
    bought(user, "Rice 10kg", "120.00", days_ago=10)
    bought(user, "Milk x6", "20.00", qty=6, days_ago=2)  # R120 for the line ties with rice: newer wins
    bought(user, "Soap", "15.00", days_ago=1)
    bought(user, "Chicken", "89.00", days_ago=30)
    bought(user, "Old fridge", "450.00", days_ago=61)  # too old
    bought(user, "Bread", "18.00", days_ago=1)
    names = [item.ItemName for item in dashboard.recently_bought(user.UserId)]
    assert names == ["Milk x6", "Rice 10kg", "Chicken", "Bread"]


def test_only_purchased_items_of_this_student_count(app, user, make_user, provider):
    other = make_user()
    bought(other, "Somebody else's TV", "999.00")
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("1000"))])
    shopping.add_product(
        user, provider.get_offers_by_barcode(MILK, HOME["lat"], HOME["lng"], 15)[0]
    )  # on the list, not bought
    assert dashboard.recently_bought(user.UserId) == []


def test_nothing_bought_is_an_empty_list(app, user):
    assert dashboard.recently_bought(user.UserId) == []


# ================================================================================================ build()
def test_build_with_no_budget(app, user):
    data = dashboard.build(user)
    assert data.budget is None and not data.is_active and data.position is None
    assert data.list_count == 0 and data.list_total == 0 and data.savings == 0 and not data.has_alternatives


def test_build_with_an_active_budget_and_list(app, user, provider):
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("1000"))])
    offer = sorted(provider.get_offers_by_barcode(MILK, HOME["lat"], HOME["lng"], 15), key=lambda o: -o.price)[0]
    shopping.add_product(user, offer)
    data = dashboard.build(user)
    assert data.is_active and data.budget.Title == "Sep"
    assert data.list_count == 1 and data.list_total == offer.price
    assert data.position.used == offer.price and data.position.total == D("1000")
    assert data.savings > 0 and data.has_alternatives  # the dearest milk has a cheaper twin nearby


def test_build_falls_back_to_the_last_closed_budget(app, user, provider):
    budgets.create_budget(user.UserId, "Aug", [("Grocery", D("900"))])
    shopping.add_product(user, provider.get_offers_by_barcode(MILK, HOME["lat"], HOME["lng"], 15)[0])
    budgets.complete_purchase(user.UserId)
    data = dashboard.build(user)
    assert not data.is_active and data.budget.Title == "Aug" and data.position.used > 0
    assert data.list_count == 0 and data.list_total == 0


def test_a_price_service_outage_does_not_break_the_dashboard(app, user, provider, monkeypatch):
    from app.services.retail_api import RetailAPIError

    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("1000"))])
    shopping.add_product(user, provider.get_offers_by_barcode(MILK, HOME["lat"], HOME["lng"], 15)[0])

    def boom(*args, **kwargs):
        raise RetailAPIError("down")

    monkeypatch.setattr(shopping, "refresh_alternatives", boom)
    assert dashboard.build(user).list_count == 1


# ================================================================================================ the page
def test_dashboard_needs_sign_in(client):
    assert client.get("/dashboard").status_code == 302
    assert client.get("/").status_code == 302


def test_root_and_dashboard_show_the_same_page(signed_in):
    assert signed_in.get("/").status_code == 200
    assert b"Welcome, Thabo" in signed_in.get("/dashboard").data


def test_empty_state_for_a_new_student(signed_in):
    html = signed_in.get("/dashboard").get_data(as_text=True)
    assert "Welcome, Thabo" in html and "👋" in html
    assert "You have not set a budget yet" in html and "Create New Budget" in html
    assert "Nothing bought yet" in html and "No active list" in html and "No budget yet" in html
    assert "Weather data by" in html and "open-meteo.com" in html


def test_active_budget_card_and_tiles(signed_in, user, provider):
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("1000"))])
    offer = sorted(provider.get_offers_by_barcode(MILK, HOME["lat"], HOME["lng"], 15), key=lambda o: -o.price)[0]
    shopping.add_product(user, offer)
    html = signed_in.get("/dashboard").get_data(as_text=True)
    assert "Active Budget" in html and "View Details" in html and 'href="/budget"' in html
    assert "Total Budget" in html and "R1 000" in html
    assert "1 item" in html and "View List" in html and 'href="/list"' in html
    assert "with cheaper alternatives" in html and "Show alternatives" in html
    assert 'id="status-ring"' in html and 'data-percent="' in html
    assert "Create New Budget" not in html


def test_the_savings_tile_says_none_found_when_there_are_none(signed_in, user, provider):
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("1000"))])
    cheapest = sorted(provider.get_offers_by_barcode(MILK, HOME["lat"], HOME["lng"], 15), key=lambda o: o.price)[0]
    shopping.add_product(user, cheapest)
    html = signed_in.get("/dashboard").get_data(as_text=True)
    assert "No cheaper alternatives found" in html


def test_a_closed_budget_shows_as_last_budget_with_a_create_button(signed_in, user, provider):
    budgets.create_budget(user.UserId, "Aug Budget", [("Grocery", D("900"))])
    shopping.add_product(user, provider.get_offers_by_barcode(MILK, HOME["lat"], HOME["lng"], 15)[0])
    budgets.complete_purchase(user.UserId)
    html = signed_in.get("/dashboard").get_data(as_text=True)
    assert "Last Budget" in html and "Aug Budget" in html and "closed" in html
    assert "Create New Budget" in html and "No active list" in html


def test_recently_bought_rows_show_name_amount_date_and_store(signed_in, user):
    bought(user, "Samsung Kettle", "649.00", store="Game", days_ago=3)
    html = signed_in.get("/dashboard").get_data(as_text=True)
    assert (
        "Samsung Kettle" in html
        and "R649" in html
        and "Game" in html
        and 'href="/history"' in html
        and "See All" in html
    )


def test_other_students_purchases_never_appear(signed_in, make_user):
    bought(make_user(), "Somebody else's TV", "999.00")
    assert "Somebody else&#39;s TV" not in signed_in.get("/dashboard").get_data(as_text=True)


def test_the_weather_card_knows_where_to_fetch_and_skips_geolocation_with_a_saved_address(signed_in):
    html = signed_in.get("/dashboard").get_data(as_text=True)
    assert 'data-url="/api/weather"' in html and 'data-can-locate="0"' in html
    assert ">Durban<" in html


def test_a_student_with_no_saved_address_is_offered_geolocation(client, make_user, login):
    login(make_user())
    html = client.get("/dashboard").get_data(as_text=True)
    assert 'data-can-locate="1"' in html and ">Durban<" in html


def test_student_names_are_escaped(client, make_user, login):
    login(make_user(first="<b>Bob</b>"))
    html = client.get("/dashboard").get_data(as_text=True)
    assert "<b>Bob</b>" not in html and "&lt;b&gt;Bob&lt;/b&gt;" in html
