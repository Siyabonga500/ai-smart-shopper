"""Steps 12-14: the shopping list page, the summary and "Done", the shopping route, and the history pages."""

import itertools
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import responses

from app.extensions import cache, db
from app.models import ListItem, ShoppingList
from app.services import budgets, history, routing, shopping
from app.services.invoice import UNKNOWN_STORE, build_invoice
from app.services.retail_api import RetailAPIError, get_retail_provider
from app.utils.dates import utcnow

D = Decimal
MILK = "2000000000275"
CHOC_MILK = "2000000000305"
HOME = {"lat": -29.8587, "lng": 31.0218}


@pytest.fixture
def user(make_user):
    return make_user(Latitude=HOME["lat"], Longitude=HOME["lng"])


@pytest.fixture
def budget(user):
    return budgets.create_budget(user.UserId, "Sep Budget", [("Grocery", D("800")), ("Toiletries", D("200"))])


@pytest.fixture
def signed_in(client, user, login):
    login(user)
    return client


@pytest.fixture
def provider(app):
    return get_retail_provider()


def offers(provider, barcode=MILK, in_stock=True):
    found = provider.get_offers_by_barcode(barcode, HOME["lat"], HOME["lng"], 15)
    return sorted([o for o in found if o.in_stock or not in_stock], key=lambda o: (o.price, o.store_name))


def add(user, offer, qty=1):
    result = shopping.add_product(user, offer)
    if qty > 1:
        shopping.set_quantity(user, result.item.ListItemId, qty)
    return result.item


def buy_trip(user, provider, barcodes=(MILK,), days_ago=3, title="Trip", entries=(("Grocery", D("900")),)):
    """A finished trip: budget, list, items bought ``days_ago`` days ago."""
    budgets.create_budget(user.UserId, title, [(c, a) for c, a in entries])
    for barcode in barcodes:
        add(user, offers(provider, barcode)[0])
    budget = budgets.get_active_budget(user.UserId)
    shopping_list = budgets.get_active_list(budget)
    budgets.complete_purchase(user.UserId)
    when = utcnow() - timedelta(days=days_ago)
    shopping_list.DateClosed = when
    for item in db.session.query(ListItem).filter_by(ShoppingListId=shopping_list.ShoppingListId):
        item.PurchasedDate = when
    db.session.commit()
    return shopping_list


# ================================================================================================ routing
def stop(name, lat, lng, items=1):
    return routing.Stop(name, lat, lng, items)


def test_the_store_with_the_fewest_items_is_first_then_the_shortest_way_round():
    near, mid, far = (
        stop("Near", -29.86, 31.03, items=5),
        stop("Mid", -29.87, 31.05, items=2),
        stop("Far", -29.85, 31.02, items=1),
    )
    order = routing.order_stops(HOME, [near, mid, far])
    assert order[0].name == "Far"  # 1 item: the fewest
    other = [s.name for s in order[1:]]
    assert set(other) == {"Near", "Mid"}
    lengths = {}
    for perm in ((near, mid), (mid, near)):
        lengths[perm[0].name] = routing._path_length(HOME, (far, *perm), True)
    assert other[0] == min(lengths, key=lengths.get)  # the shorter of the two orders


def test_ties_on_item_count_pick_the_shorter_trip_and_are_deterministic():
    a, b, c = stop("A", -29.90, 31.10, 2), stop("B", -29.86, 31.03, 2), stop("C", -29.88, 31.06, 2)
    first = routing.order_stops(HOME, [a, b, c])
    assert [s.name for s in first] == [s.name for s in routing.order_stops(HOME, [c, b, a])]
    best = min(routing._path_length(HOME, p, True) for p in itertools.permutations([a, b, c]))
    assert routing._path_length(HOME, first, True) == pytest.approx(best)


def test_zero_or_one_store_needs_no_ordering():
    assert routing.order_stops(HOME, []) == []
    only = stop("Only", -29.9, 31.0)
    assert routing.order_stops(HOME, [only]) == [only]


def test_many_stores_fall_back_to_nearest_neighbour_from_the_fewest_items_store():
    stores = [stop(f"S{i}", -29.85 - i * 0.01, 31.0 + i * 0.01, items=(1 if i == 4 else 3)) for i in range(9)]
    order = routing.order_stops(HOME, stores)
    assert len(order) == 9 and order[0].name == "S4" and {s.name for s in order} == {s.name for s in stores}


OSRM_URL = "http://osrm.test"


@pytest.fixture
def osrm(app):
    app.config["OSRM_BASE_URL"] = OSRM_URL
    cache.clear()
    return OSRM_URL


@responses.activate
def test_road_route_uses_osrm_figures_and_the_drawn_line(app, osrm):
    order = [stop("A", -29.86, 31.03), stop("B", -29.88, 31.06)]
    responses.get(
        f"{OSRM_URL}/route/v1/driving/31.021800,-29.858700;31.030000,-29.860000;31.060000,-29.880000;31.021800,-29.858700",
        json={
            "code": "Ok",
            "routes": [
                {
                    "distance": 7712.0,
                    "duration": 1080.0,
                    "geometry": {"type": "LineString", "coordinates": [[31.0218, -29.8587], [31.03, -29.86]]},
                }
            ],
        },
    )
    route = routing.road_route(HOME, order, return_home=True)
    assert route == {
        "distance_km": 7.7,
        "duration_min": 18,
        "geometry": [[-29.8587, 31.0218], [-29.86, 31.03]],
        "source": "osrm",
    }


@responses.activate
@pytest.mark.parametrize(
    "reply",
    [
        {"status": 500},
        {"status": 200, "json": {"code": "NoRoute"}},
        {"status": 200, "body": "not json"},
        {"status": 200, "json": {"code": "Ok", "routes": [{"distance": 1}]}},
        {"status": 404},
    ],
)
def test_osrm_trouble_gives_a_marked_estimate_not_an_error(app, osrm, reply):
    responses.get(
        f"{OSRM_URL}/route/v1/driving/31.021800,-29.858700;31.030000,-29.860000;31.021800,-29.858700", **reply
    )
    route = routing.road_route(HOME, [stop("A", -29.86, 31.03)], return_home=True)
    assert route["source"] == "estimate" and route["distance_km"] > 0 and route["duration_min"] >= 1
    assert route["geometry"][0] == [HOME["lat"], HOME["lng"]]


def test_the_estimate_uses_the_road_factor_and_city_speed(app):
    points = [(HOME["lat"], HOME["lng"]), (-29.86, 31.03)]
    from app.utils.geo import distance_km

    straight = distance_km(*points[0], *points[1])
    estimate = routing._estimate(points)
    assert estimate["distance_km"] == pytest.approx(round(straight * routing.ROAD_FACTOR, 1))
    assert estimate["duration_min"] == max(1, round(straight * routing.ROAD_FACTOR / routing.CITY_SPEED_KMH * 60))


def test_plan_reports_stores_without_a_position(app, user, budget, provider, osrm):
    a = add(user, offers(provider)[0])
    b = add(user, offers(provider)[-1])
    b.StoreLatitude = b.StoreLongitude = None
    db.session.commit()
    invoice = build_invoice(budgets.list_items(budgets.get_active_list(budget)))
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.get(
            f"{OSRM_URL}/route/v1/driving/31.021800,-29.858700;{a.StoreLongitude:.6f},{a.StoreLatitude:.6f};31.021800,-29.858700",
            json={
                "code": "Ok",
                "routes": [{"distance": 2000, "duration": 300, "geometry": {"coordinates": [[31.0, -29.8]]}}],
            },
        )
        payload = routing.plan(HOME, invoice.groups)
    assert [s["name"] for s in payload["stops"]] == [a.StoreName] and payload["unlocated"] == [b.StoreName]


def test_api_route_needs_a_list_and_returns_ordered_stops(app, signed_in, user, budget, provider, osrm):
    assert signed_in.get("/api/route").status_code == 409
    for offer in offers(provider)[:3]:
        add(user, offer)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.get(
            re.compile(rf"{OSRM_URL}/route/v1/driving/.*"),
            json={
                "code": "Ok",
                "routes": [
                    {"distance": 12345, "duration": 1500, "geometry": {"coordinates": [[31.0, -29.8], [31.1, -29.9]]}}
                ],
            },
        )
        data = signed_in.get("/api/route").get_json()
    assert data["source"] == "osrm" and data["distance_km"] == 12.3 and data["duration_min"] == 25
    assert (
        [s["order"] for s in data["stops"]] == [1, 2, 3] and data["return_home"] is True and data["home_known"] is True
    )
    assert data["home"] == HOME


def test_api_route_requires_sign_in(client):
    assert client.get("/api/route").status_code in (302, 401)


# ================================================================================================ invoice
def test_invoice_groups_by_store_with_subtotals(app, user, budget, provider):
    o = offers(provider)
    add(user, o[0], qty=2)
    add(user, o[1])
    add(user, offers(provider, CHOC_MILK)[0])
    invoice = build_invoice(budgets.list_items(budgets.get_active_list(budget)))
    assert invoice.store_count == 2 and invoice.item_count == 3 and invoice.units == 4
    assert invoice.total == sum((g.subtotal for g in invoice.groups), D("0"))
    assert all(g.subtotal == sum((r["line_total"] for r in g.rows), D("0")) for g in invoice.groups)
    first_store = invoice.groups[1].store_name
    reordered = build_invoice(budgets.list_items(budgets.get_active_list(budget)), order=[first_store])
    assert reordered.groups[0].store_name == first_store


def test_items_without_a_store_go_in_their_own_group(app, user, budget, provider):
    item = add(user, offers(provider)[0])
    item.StoreName = None
    db.session.commit()
    invoice = build_invoice([item])
    assert invoice.groups[0].store_name == UNKNOWN_STORE and invoice.store_count == 1


# ================================================================================================ the list page
def test_list_page_without_a_budget_offers_to_create_one(signed_in):
    html = signed_in.get("/list").get_data(as_text=True)
    assert "Create New Budget" in html and "list-body" not in html


def test_list_page_shows_the_budget_position_and_items(signed_in, user, budget, provider):
    offer = offers(provider)[0]
    add(user, offer, qty=2)
    html = signed_in.get("/list").get_data(as_text=True)
    assert "Sep Budget Shopping List" in html and offer.name in html and "Estimated Total" in html
    assert (
        "Proceed to Summary" in html
        and "/list/summary" in html
        and "disabled" not in html.split("Proceed to Summary")[0][-200:]
    )
    assert "Cheaper alternative available" not in html  # hidden until asked for or over budget


def test_alternatives_show_on_request_and_automatically_when_over_budget(signed_in, user, budget, provider):
    dear = offers(provider)[-1]
    item = add(user, dear)
    assert "Cheaper alternative available" not in signed_in.get("/list").get_data(as_text=True)
    asked = signed_in.get("/list?alternatives=1").get_data(as_text=True)
    assert "Cheaper alternative available" in asked and "Replace" in asked
    shopping.set_quantity(user, item.ListItemId, 99)  # far over the R1 000 budget
    over = signed_in.get("/list").get_data(as_text=True)
    assert "Shopping List Over Budget" in over and "Cheaper alternative available" in over
    assert "Your shopping list is over budget. Replace items with cheaper alternatives or reduce quantities." in over
    assert 'href="/list/summary"' not in over


def test_the_fragment_is_marked_and_not_cached(signed_in, user, budget, provider):
    add(user, offers(provider)[0])
    response = signed_in.get("/list/fragment")
    assert (
        response.status_code == 200
        and response.headers["X-List-Fragment"] == "1"
        and response.headers["Cache-Control"] == "no-store"
    )
    assert b"<html" not in response.data and b"Estimated Total" in response.data


def test_the_old_shopping_address_redirects_to_list(client):
    for path in ("/shopping", "/shopping/"):
        response = client.get(path)
        assert response.status_code == 302 and response.headers["Location"].endswith("/list")


def test_the_replaced_product_is_named_on_the_row(signed_in, user, budget, provider):
    item = add(user, offers(provider)[-1])
    shopping.replace_with_alternative(provider, user, item.ListItemId)
    assert "Replaced" in signed_in.get("/list").get_data(as_text=True)


# ================================================================================================ summary and Done
def test_summary_is_only_for_a_list_that_can_be_shopped(signed_in, user, provider):
    assert signed_in.get("/list/summary").headers["Location"].endswith("/budget")
    budgets.create_budget(user.UserId, "B", [("Grocery", D("800"))])
    empty = signed_in.get("/list/summary")
    assert empty.status_code == 302 and empty.headers["Location"].endswith("/list")
    item = add(user, offers(provider)[-1])
    shopping.set_quantity(user, item.ListItemId, 99)
    over = signed_in.get("/list/summary", follow_redirects=True).get_data(as_text=True)
    assert "over budget" in over and "Budget Impact" not in over


def test_summary_shows_invoice_budget_impact_and_stores(signed_in, user, budget, provider):
    o = offers(provider)
    add(user, o[0], qty=2)
    add(user, offers(provider, CHOC_MILK)[1])
    html = signed_in.get("/list/summary").get_data(as_text=True)
    for text in (
        "Total Estimated Cost",
        "Budget Impact",
        "Total Budget",
        "Already Used",
        "This Shopping",
        "Remaining After Shopping",
        "Stores to visit",
        "Done – Purchase Completed",
        "route-map",
    ):
        assert text in html
    position = budgets.position(budget)
    assert f"{position.in_list:.2f}".replace(".00", "").split(".")[0] in html.replace("\xa0", "")
    stores = {i.StoreName for i in budgets.list_items(budgets.get_active_list(budget))}
    numbers = re.findall(r'class="stop-number\s*"[^>]*>(\d+)<', html)
    assert numbers == [str(n) for n in range(1, len(stores) + 1)]  # each store numbered in visiting order


def test_category_impact_separates_previous_spend_from_this_trip(app, user, budget, provider):
    add(user, offers(provider)[0])  # Grocery
    items = budgets.list_items(budgets.get_active_list(budget))
    impact = {row.category: row for row in budgets.category_impact(budget, items)}
    grocery = impact["Grocery"]
    assert grocery.this_shopping == items[0].LineTotal and grocery.previously_used == 0
    assert grocery.remaining == D("800") - grocery.this_shopping
    assert impact["Toiletries"].this_shopping == 0


def test_done_closes_everything_and_records_the_purchase(signed_in, user, budget, provider):
    add(user, offers(provider)[0], qty=2)
    add(user, offers(provider, CHOC_MILK)[0])
    shopping_list = budgets.get_active_list(budget)
    spent = shopping_list.TotalCost
    response = signed_in.post("/list/complete")
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard")
    db.session.expire_all()
    assert budget.IsActive is False and budget.ClosedDate is not None
    assert shopping_list.IsActive is False and shopping_list.DateClosed is not None
    items = db.session.query(ListItem).filter_by(ShoppingListId=shopping_list.ShoppingListId).all()
    assert len(items) == 2 and all(i.IsPurchased and i.PurchasedDate is not None for i in items)
    assert budgets.get_active_budget(user.UserId) is None
    page = signed_in.get("/dashboard").get_data(as_text=True)
    assert "Purchase completed" in page and str(spent).rstrip("0").rstrip(".") in page.replace("\xa0", "")


def test_done_twice_does_not_record_the_trip_twice(signed_in, user, budget, provider):
    add(user, offers(provider)[0])
    signed_in.post("/list/complete")
    again = signed_in.post("/list/complete")
    assert again.status_code == 302 and again.headers["Location"].endswith("/list")
    assert db.session.query(ShoppingList).filter_by(UserId=user.UserId, IsActive=False).count() == 1


def test_done_is_refused_for_an_empty_or_over_budget_list(signed_in, user, provider):
    budgets.create_budget(user.UserId, "B", [("Grocery", D("800"))])
    assert signed_in.post("/list/complete").headers["Location"].endswith("/list")
    item = add(user, offers(provider)[-1])
    shopping.set_quantity(user, item.ListItemId, 99)
    assert signed_in.post("/list/complete").headers["Location"].endswith("/list")
    assert (
        budgets.get_active_budget(user.UserId) is not None
        and db.session.get(ListItem, item.ListItemId).IsPurchased is False
    )


def test_done_only_touches_the_signed_in_students_data(signed_in, user, budget, provider, make_user):
    other = make_user(first="Zanele", Latitude=HOME["lat"], Longitude=HOME["lng"])
    budgets.create_budget(other.UserId, "Theirs", [("Grocery", D("500"))])
    add(other, offers(provider)[0])
    add(user, offers(provider)[0])
    signed_in.post("/list/complete")
    assert budgets.get_active_budget(other.UserId) is not None
    assert db.session.query(ListItem).filter_by(UserId=other.UserId, IsPurchased=True).count() == 0


# ================================================================================================ history
def test_history_lists_finished_trips_newest_first_with_totals(app, user, provider):
    buy_trip(user, provider, (MILK,), days_ago=40, title="Old")
    buy_trip(user, provider, (MILK, CHOC_MILK), days_ago=2, title="Recent")
    page = history.find_trips(user.UserId, history.HistoryFilters())
    assert [t.title for t in page.trips] == ["Recent Shopping List", "Old Shopping List"]
    recent = page.trips[0]
    assert recent.item_count == 2 and recent.total == sum((i.LineTotal for i in recent.items), D("0"))
    assert recent.store_count >= 1


def test_abandoned_and_open_lists_are_not_history(app, user, provider):
    budgets.create_budget(user.UserId, "Removed", [("Grocery", D("500"))])
    add(user, offers(provider)[0])
    budgets.remove_active_budget(user.UserId)
    budgets.create_budget(user.UserId, "Open", [("Grocery", D("500"))])
    add(user, offers(provider)[0])
    assert history.find_trips(user.UserId, history.HistoryFilters()).total == 0


def test_history_filters(app, user, provider):
    buy_trip(user, provider, (MILK,), days_ago=40, title="Old")
    buy_trip(user, provider, (CHOC_MILK,), days_ago=2, title="Recent")
    everything = history.find_trips(user.UserId, history.HistoryFilters())
    old, recent = everything.trips[1], everything.trips[0]
    today = date.today()
    only_recent = history.find_trips(user.UserId, history.HistoryFilters(date_from=today - timedelta(days=10)))
    assert [t.id for t in only_recent.trips] == [recent.id]
    only_old = history.find_trips(user.UserId, history.HistoryFilters(date_to=today - timedelta(days=20)))
    assert [t.id for t in only_old.trips] == [old.id]
    store = old.items[0].StoreName
    by_store = history.find_trips(user.UserId, history.HistoryFilters(store=store))
    assert old.id in [t.id for t in by_store.trips]
    assert history.find_trips(user.UserId, history.HistoryFilters(store="Nowhere")).total == 0
    assert history.find_trips(user.UserId, history.HistoryFilters(category="Grocery")).total == 2
    assert history.find_trips(user.UserId, history.HistoryFilters(category="Clothes")).total == 0
    options = history.filter_options(user.UserId)
    assert options["categories"] == ["Grocery"] and store in options["stores"]


def test_the_end_date_is_inclusive_in_south_african_time(app, user, provider):
    trip = buy_trip(user, provider, days_ago=0, title="Late")
    trip.DateClosed = datetime(2026, 9, 10, 21, 30, tzinfo=timezone.utc)  # 23:30 on the 10th in SAST
    db.session.commit()
    on_the_day = history.HistoryFilters(date_from=date(2026, 9, 10), date_to=date(2026, 9, 10))
    assert history.find_trips(user.UserId, on_the_day).total == 1
    assert history.find_trips(user.UserId, history.HistoryFilters(date_from=date(2026, 9, 11))).total == 0


def test_filter_parsing_reports_bad_input_and_never_crashes(app):
    from werkzeug.datastructures import MultiDict

    f, errors = history.parse_filters(
        MultiDict({"from": "2026-13-45", "to": "2026-09-01", "store": "  Shoprite   X ", "page": "zzz"})
    )
    assert "from" in errors and f.date_from is None and f.store == "Shoprite X" and f.page == 1
    f, errors = history.parse_filters(MultiDict({"from": "2026-09-10", "to": "2026-09-01"}))
    assert "to" in errors and f.date_from is None and f.date_to is None


def test_paging_and_month_groups(app, user, provider):
    for n in range(12):
        buy_trip(user, provider, days_ago=n * 3, title=f"T{n}")
    first = history.find_trips(user.UserId, history.HistoryFilters(), page_size=5)
    assert first.total == 12 and first.pages == 3 and len(first.trips) == 5
    last = history.find_trips(user.UserId, history.HistoryFilters(page=99), page_size=5)
    assert last.page == 3 and len(last.trips) == 2
    labels = [label for label, _ in history.group_by_month(first.trips)]
    assert labels and all(len(label.split()) == 2 for label in labels)


def test_history_pages(signed_in, user, provider, make_user):
    buy_trip(user, provider, (MILK, CHOC_MILK), days_ago=2, title="Recent")
    listing = signed_in.get("/history").get_data(as_text=True)
    assert "Recent Shopping List" in listing and "2 items" in listing
    trip = history.find_trips(user.UserId, history.HistoryFilters()).trips[0]
    detail = signed_in.get(f"/history/{trip.id}").get_data(as_text=True)
    assert "Read only" in detail and "Total Spent" in detail and trip.items[0].ItemName in detail
    assert "Reorder" in detail and 'name="mode" value="replace"' in detail
    assert signed_in.get("/history/LST-doesnotexist").status_code == 404
    filtered = signed_in.get("/history?store=Nowhere").get_data(as_text=True)
    assert "No trips match these filters" in filtered
    bad = signed_in.get("/history?from=nonsense")
    assert bad.status_code == 200 and "Use a date like" in bad.get_data(as_text=True)


def test_history_empty_state_and_privacy(signed_in, user, provider, make_user):
    assert "No shopping trips yet" in signed_in.get("/history").get_data(as_text=True)
    other = make_user(first="Zanele", Latitude=HOME["lat"], Longitude=HOME["lng"])
    buy_trip(other, provider, days_ago=1)
    theirs = history.find_trips(other.UserId, history.HistoryFilters()).trips[0]
    assert signed_in.get(f"/history/{theirs.id}").status_code == 404
    assert signed_in.post(f"/history/{theirs.id}/reorder").status_code == 404
    assert history.find_trips(user.UserId, history.HistoryFilters()).total == 0


# ---------------------------------------------------------------------------------------------- reorder
def test_reorder_uses_todays_prices_and_says_what_changed(signed_in, user, provider):
    trip_list = buy_trip(user, provider, (MILK, CHOC_MILK), days_ago=5)
    milk_item, choc_item = sorted(
        db.session.query(ListItem).filter_by(ShoppingListId=trip_list.ShoppingListId), key=lambda i: i.BarCode
    )
    old_milk_price = milk_item.UnitCost
    milk_item.UnitCost = old_milk_price - D("2.00")  # cost R2 less last time: now it has gone up
    choc_item.UnitCost = choc_item.UnitCost + D("3.00")  # cost R3 more last time: now it is cheaper
    db.session.commit()
    budgets.create_budget(user.UserId, "October", [("Grocery", D("900"))])
    response = signed_in.post(f"/history/{trip_list.ShoppingListId}/reorder", data={"mode": "replace"})
    assert response.status_code == 302 and response.headers["Location"].endswith("/list")
    flashed = signed_in.get("/list").get_data(as_text=True)
    assert "1 item increased in price, 1 decreased." in flashed and "reordered at today" in flashed
    new_items = budgets.list_items(budgets.get_active_list(budgets.get_active_budget(user.UserId)))
    assert {i.BarCode for i in new_items} == {MILK, CHOC_MILK}
    cheapest_today = {b: offers(provider, b)[0].price for b in (MILK, CHOC_MILK)}
    same_store = {i.BarCode: i.UnitCost for i in new_items}
    assert same_store[MILK] == old_milk_price  # priced from the provider today, not from the old row
    assert all(i.IsPurchased is False and i.ItemQuantity == 1 for i in new_items)
    assert cheapest_today[MILK] <= same_store[MILK]


def test_reorder_reports_unavailable_items_and_keeps_the_rest(signed_in, user, provider):
    trip_list = buy_trip(user, provider, (MILK, CHOC_MILK), days_ago=5)
    vanished = db.session.query(ListItem).filter_by(ShoppingListId=trip_list.ShoppingListId, BarCode=CHOC_MILK).one()
    vanished.BarCode = "0000000000000"
    vanished.ItemName = "Discontinued Thing"
    db.session.commit()
    budgets.create_budget(user.UserId, "October", [("Grocery", D("900"))])
    signed_in.post(f"/history/{trip_list.ShoppingListId}/reorder", data={"mode": "replace"})
    page = signed_in.get("/list").get_data(as_text=True)
    assert "1 item is no longer available and was left out (Discontinued Thing)" in page
    items = budgets.list_items(budgets.get_active_list(budgets.get_active_budget(user.UserId)))
    assert [i.BarCode for i in items] == [MILK]


def test_reorder_when_nothing_can_be_found_changes_nothing(signed_in, user, provider):
    trip_list = buy_trip(user, provider, (MILK,), days_ago=5)
    for item in db.session.query(ListItem).filter_by(ShoppingListId=trip_list.ShoppingListId):
        item.BarCode = "0000000000000"
    db.session.commit()
    budgets.create_budget(user.UserId, "October", [("Grocery", D("900"))])
    keep = add(user, offers(provider, CHOC_MILK)[0])
    response = signed_in.post(f"/history/{trip_list.ShoppingListId}/reorder", data={"mode": "replace"})
    assert response.headers["Location"].endswith(f"/history/{trip_list.ShoppingListId}")
    assert db.session.get(ListItem, keep.ListItemId) is not None  # the list was not cleared


def test_reorder_replace_versus_merge(signed_in, user, provider):
    trip_list = buy_trip(user, provider, (MILK,), days_ago=5)
    budgets.create_budget(user.UserId, "October", [("Grocery", D("900"))])
    add(user, offers(provider, CHOC_MILK)[0])
    signed_in.post(f"/history/{trip_list.ShoppingListId}/reorder", data={"mode": "merge"})
    db.session.expire_all()
    items = budgets.list_items(budgets.get_active_list(budgets.get_active_budget(user.UserId)))
    assert {i.BarCode for i in items} == {MILK, CHOC_MILK}
    signed_in.post(
        f"/history/{trip_list.ShoppingListId}/reorder", data={"mode": "merge"}
    )  # again: quantity goes up, no duplicate row
    db.session.expire_all()
    items = budgets.list_items(budgets.get_active_list(budgets.get_active_budget(user.UserId)))
    assert len(items) == 2 and next(i for i in items if i.BarCode == MILK).ItemQuantity == 2
    signed_in.post(f"/history/{trip_list.ShoppingListId}/reorder", data={"mode": "replace"})
    db.session.expire_all()
    items = budgets.list_items(budgets.get_active_list(budgets.get_active_budget(user.UserId)))
    assert [i.BarCode for i in items] == [MILK] and items[0].ItemQuantity == 1


def test_reorder_needs_an_active_budget(signed_in, user, provider):
    trip_list = buy_trip(user, provider, (MILK,), days_ago=5)
    response = signed_in.post(f"/history/{trip_list.ShoppingListId}/reorder")
    assert response.status_code == 302 and response.headers["Location"].endswith("/budget/new")


def test_reorder_keeps_quantities_and_keeps_the_budget_totals_right(signed_in, user, provider):
    budgets.create_budget(user.UserId, "Trip", [("Grocery", D("900"))])
    add(user, offers(provider)[0], qty=3)
    trip_list = budgets.get_active_list(budgets.get_active_budget(user.UserId))
    budgets.complete_purchase(user.UserId)
    budget = budgets.create_budget(user.UserId, "October", [("Grocery", D("900"))])
    signed_in.post(f"/history/{trip_list.ShoppingListId}/reorder")
    db.session.expire_all()
    item = budgets.list_items(budgets.get_active_list(budget))[0]
    assert item.ItemQuantity == 3
    assert budget.UsedAmount == item.LineTotal == budgets.get_active_list(budget).TotalCost


def test_a_provider_outage_during_reorder_changes_nothing(signed_in, user, provider, monkeypatch):
    trip_list = buy_trip(user, provider, (MILK,), days_ago=5)
    budgets.create_budget(user.UserId, "October", [("Grocery", D("900"))])
    keep = add(user, offers(provider, CHOC_MILK)[0])

    def down(*args, **kwargs):
        raise RetailAPIError("down")

    monkeypatch.setattr(provider.__class__, "get_offers_by_barcode", down)
    response = signed_in.post(f"/history/{trip_list.ShoppingListId}/reorder", data={"mode": "replace"})
    assert response.status_code == 302 and response.headers["Location"].endswith(f"/history/{trip_list.ShoppingListId}")
    monkeypatch.undo()
    assert db.session.get(ListItem, keep.ListItemId) is not None


def test_reorder_summary_wording():
    from app.services.history import ReorderLine, ReorderResult

    def line(old, new):
        return ReorderLine("X", 1, D(old), D(new) if new else None, "S", "S" if new else None)

    assert (
        ReorderResult([line("10", "12"), line("10", "11"), line("10", "9")]).summary()
        == "2 items increased in price, 1 decreased."
    )
    assert ReorderResult([line("10", "9")]).summary() == "1 item decreased in price."
    assert ReorderResult([line("10", "10")]).summary() == "No prices have changed."
    assert (
        ReorderResult([line("10", "12"), line("10", None)])
        .summary()
        .startswith("1 item increased in price. 1 item is no longer available")
    )


# ================================================================================================ migration data
def test_profile_stats_count_only_purchased_items(app, user, provider, signed_in):
    buy_trip(user, provider, (MILK,), days_ago=2)
    budgets.create_budget(user.UserId, "Open", [("Grocery", D("500"))])
    add(user, offers(provider, CHOC_MILK)[0])
    from app.services.account_stats import account_stats

    stats = account_stats(user.UserId)
    assert stats.total_spent == offers(provider)[0].price and [p.BarCode for p in stats.recent_purchases] == [MILK]
    assert "Shopping history" in signed_in.get("/profile").get_data(as_text=True)


@pytest.mark.parametrize("query", ["from=0001-01-01", "to=9999-12-31", "from=0001-01-01&to=9999-12-31"])
def test_absurd_date_filters_are_not_a_server_error(signed_in, query):
    assert signed_in.get(f"/history?{query}").status_code == 200


def test_day_bounds_stay_in_range():
    from datetime import date

    from app.utils.dates import sast_day_bounds

    lower, upper = sast_day_bounds(date(1, 1, 1), date(9999, 12, 31))
    assert lower.year == 1899 or lower.year == 1900
    assert upper.year == 2200 or upper.year == 2201
