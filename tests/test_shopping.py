"""Steps 11 and 12 back end: product helpers, search, the shopping list service and its JSON API,
cheaper alternatives, and the "Recommended for you" strip."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.cli import upsert_stores
from app.data.durban_stores import DURBAN_STORES
from app.extensions import db, search_limiter
from app.models import ListItem, Store
from app.services import budgets, products, recommendations, search, shopping
from app.services.retail_api import get_retail_provider
from app.utils.dates import utcnow

D = Decimal
MILK = "2000000000275"  # Clover Long Life Full Cream Milk 1L, sold by several retailers in the mock catalogue
CHOC_MILK = "2000000000305"  # Clover Mooo Chocolate Milk 500ml


@pytest.fixture
def user(make_user):
    return make_user(Latitude=-29.8587, Longitude=31.0218)


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


def offers_for(provider, user, barcode=MILK):
    center = shopping.student_center(user)
    return sorted(
        provider.get_offers_by_barcode(barcode, center["lat"], center["lng"], 15), key=lambda o: (o.price, o.store_name)
    )


def add_body(offer):
    return {"barcode": offer.barcode, "store_id": offer.store_id, "store_name": offer.store_name, "name": offer.name}


# -------------------------------------------------------------------------------------------------- product helpers
@pytest.mark.parametrize(
    "name,size",
    [
        ("Clover Full Cream Milk 2L", "2l"),
        ("Tastic Rice 1kg", "1kg"),
        ("Coke 330 ml", "330ml"),
        ("Bread", None),
        ("Milk 1,5L", "1.5l"),
    ],
)
def test_size_token(name, size):
    assert products.size_token(name) == size


def test_key_words_drop_brand_and_size():
    assert products.key_words("Clover Long Life Full Cream Milk 1L") == ["Long", "Life", "Full", "Cream", "Milk"]


def test_savings_text_names_the_cheaper_store(app, user, provider):
    offers = [o for o in offers_for(provider, user) if o.in_stock]
    assert len(offers) >= 2
    cheapest, dearest = offers[0], offers[-1]
    info = products.savings_text(dearest, offers)
    assert info["kind"] == "save" and info["text"].startswith("Save R")
    assert (cheapest.retailer or cheapest.store_name) in info["text"]
    assert products.savings_text(cheapest, offers)["kind"] == "cheapest"
    assert products.savings_text(cheapest, [cheapest])["kind"] == "single"


# -------------------------------------------------------------------------------------------------- adding to the list
def test_add_creates_then_increments_and_recalculates_the_budget(app, user, budget, provider):
    offer = offers_for(provider, user)[0]
    first = shopping.add_product(user, offer)
    assert first.created and first.item.ItemQuantity == 1
    second = shopping.add_product(user, offer)
    assert not second.created and second.item.ItemQuantity == 2
    assert db.session.query(ListItem).filter_by(UserId=user.UserId).count() == 1
    db.session.refresh(budget)
    shopping_list = budgets.get_active_list(budget)
    assert shopping_list.TotalCost == offer.price * 2
    assert budget.UsedAmount == offer.price * 2  # nothing else was bought earlier
    assert second.position.in_list == offer.price * 2


def test_same_barcode_at_another_store_is_a_separate_row(app, user, budget, provider):
    offers = [o for o in offers_for(provider, user) if o.in_stock]
    shopping.add_product(user, offers[0])
    result = shopping.add_product(user, offers[1])
    assert result.created
    assert db.session.query(ListItem).filter_by(UserId=user.UserId).count() == 2


def test_item_is_filled_from_the_offer_and_linked_to_its_category(app, user, budget, provider):
    offer = offers_for(provider, user)[0]
    item = shopping.add_product(user, offer).item
    assert (item.ItemName, item.BarCode, item.StoreName, item.Category) == (
        offer.name,
        offer.barcode,
        offer.store_name,
        "Grocery",
    )
    assert item.UnitCost == offer.price
    assert item.SubBudgetId == budgets.sub_budget_for(budget, "Grocery").SubBudgetId
    assert item.has_store_location and item.IsPurchased is False


def test_over_budget_never_blocks_an_add_but_carries_the_alert(app, user, provider):
    tiny = budgets.create_budget(user.UserId, "Tiny", [("Grocery", D("10"))])
    offer = offers_for(provider, user)[0]
    result = shopping.add_product(user, offer)  # the item costs more than R10
    assert result.item is not None and result.position.is_over
    assert result.alert == f"Your shopping list has exceeded your allocated budget by R{offer.price - 10}."
    db.session.refresh(tiny)
    assert tiny.UsedAmount == offer.price


def test_add_without_a_budget_is_a_409(app, user, provider):
    with pytest.raises(shopping.ShoppingError) as exc:
        shopping.add_product(user, offers_for(provider, user)[0])
    assert exc.value.status == 409


def test_quantity_cap(app, user, budget, provider):
    offer = offers_for(provider, user)[0]
    item = shopping.add_product(user, offer).item
    shopping.set_quantity(user, item.ListItemId, app.config["MAX_ITEM_QUANTITY"])
    with pytest.raises(shopping.ShoppingError):
        shopping.add_product(user, offer)
    for bad in (0, -1, app.config["MAX_ITEM_QUANTITY"] + 1):
        with pytest.raises(shopping.ShoppingError):
            shopping.set_quantity(user, item.ListItemId, bad)


def test_remove_and_rename(app, user, budget, provider):
    offer = offers_for(provider, user)[0]
    item = shopping.add_product(user, offer).item
    pos = shopping.remove_item(user, item.ListItemId)
    assert pos.in_list == 0 and db.session.get(ListItem, item.ListItemId) is None
    assert shopping.rename_list(user, "  My   weekly shop ").Title == "My weekly shop"
    with pytest.raises(shopping.ShoppingError):
        shopping.rename_list(user, "   ")
    with pytest.raises(shopping.ShoppingError):
        shopping.rename_list(user, "x" * 101)


def test_a_student_cannot_touch_another_students_items(app, user, budget, provider, make_user):
    offer = offers_for(provider, user)[0]
    item = shopping.add_product(user, offer).item
    other = make_user(first="Zanele")
    budgets.create_budget(other.UserId, "Theirs", [("Grocery", D("500"))])
    for call in (
        lambda: shopping.set_quantity(other, item.ListItemId, 3),
        lambda: shopping.remove_item(other, item.ListItemId),
    ):
        with pytest.raises(shopping.ShoppingError) as exc:
            call()
        assert exc.value.status == 404


# -------------------------------------------------------------------------------------------------- the JSON API
def test_api_requires_sign_in(client):
    for url in ("/api/search?q=milk", "/api/list", "/api/recommendations", f"/api/product?barcode={MILK}"):
        assert client.get(url).status_code in (302, 401)
    assert client.post("/api/list/items", json={}).status_code in (302, 400, 401)


def test_api_add_uses_the_servers_price_not_the_browsers(signed_in, user, budget, provider):
    offer = offers_for(provider, user)[0]
    body = {**add_body(offer), "price": "0.01", "unit_cost": "0.01"}  # a tampered price is ignored
    response = signed_in.post("/api/list/items", json=body)
    assert response.status_code == 201
    data = response.get_json()
    assert data["created"] is True and D(data["added"]["unit_cost"]) == offer.price
    assert data["budget"]["in_list"] == str(offer.price)
    again = signed_in.post("/api/list/items", json=body)
    assert again.status_code == 200 and again.get_json()["quantity"] == 2


def test_api_add_validation(signed_in, user, budget):
    assert signed_in.post("/api/list/items", json={}).status_code == 400
    assert (
        signed_in.post("/api/list/items", json={"barcode": "0000000000000", "store_name": "Nowhere"}).status_code == 404
    )


def test_api_add_without_budget_points_to_create_budget(signed_in, user, provider):
    offer = offers_for(provider, user)[0]
    response = signed_in.post("/api/list/items", json=add_body(offer))
    assert response.status_code == 409
    assert response.get_json()["create_budget_url"].endswith("/budget/new")


def test_api_add_over_budget_alerts_without_blocking(signed_in, user, provider):
    budgets.create_budget(user.UserId, "Tiny", [("Grocery", D("5"))])
    offer = offers_for(provider, user)[0]
    response = signed_in.post("/api/list/items", json=add_body(offer))
    assert response.status_code == 201
    data = response.get_json()
    assert data["over_budget"] is True
    assert data["alert"] == f"Your shopping list has exceeded your allocated budget by R{offer.price - 5}."


def test_api_out_of_stock_is_refused(signed_in, user, budget, provider):
    center = shopping.student_center(user)
    out = next(
        (
            o
            for term in ("milk", "rice", "bread", "sugar", "soap")
            for o in provider.search_products(term, center["lat"], center["lng"], 15)
            if not o.in_stock
        ),
        None,
    )
    if out is None:
        pytest.skip("no out-of-stock product in the mock catalogue near Durban")
    response = signed_in.post("/api/list/items", json=add_body(out))
    assert response.status_code == 409 and "out of stock" in response.get_json()["error"]


def test_api_quantity_and_delete(signed_in, user, budget, provider):
    item_id = signed_in.post("/api/list/items", json=add_body(offers_for(provider, user)[0])).get_json()["added"]["id"]
    response = signed_in.patch(f"/api/list/items/{item_id}", json={"quantity": 3})
    assert response.status_code == 200 and response.get_json()["items"][0]["quantity"] == 3
    for bad in ({"quantity": 0}, {"quantity": "x"}, {"quantity": True}, {"quantity": 1.5}, {}):
        assert signed_in.patch(f"/api/list/items/{item_id}", json=bad).status_code == 400
    assert signed_in.delete(f"/api/list/items/{item_id}").get_json()["items"] == []
    assert signed_in.delete(f"/api/list/items/{item_id}").status_code == 404


def test_api_list_and_rename(signed_in, user, budget, provider):
    signed_in.post("/api/list/items", json=add_body(offers_for(provider, user)[0]))
    data = signed_in.get("/api/list").get_json()
    assert data["list"]["title"] == "Sep Budget Shopping List" and data["list"]["count"] == 1
    assert data["budget"]["total"] == "1000.00"
    renamed = signed_in.patch("/api/list", json={"title": "Weekend shop"})
    assert renamed.status_code == 200 and renamed.get_json()["list"]["title"] == "Weekend shop"
    assert signed_in.patch("/api/list", json={"title": ""}).status_code == 400


def test_api_product_details_lists_every_store_cheapest_first(signed_in, user, provider):
    offers = [o for o in offers_for(provider, user) if o.in_stock]
    response = signed_in.get(f"/api/product?barcode={MILK}&store_id={offers[-1].store_id}")
    assert response.status_code == 200
    data = response.get_json()
    prices = [D(o["price"]) for o in data["offers"] if o["in_stock"]]
    assert prices == sorted(prices)
    assert data["product"]["store_id"] == offers[-1].store_id
    assert data["cheapest_key"] == data["offers"][0]["key"]
    assert data["savings"]["kind"] == "save" and "Save R" in data["savings"]["text"]
    assert signed_in.get("/api/product").status_code == 400
    assert signed_in.get("/api/product?barcode=0000000000000").status_code == 404


# -------------------------------------------------------------------------------------------------- search
def test_search_parameter_validation(app):
    from werkzeug.datastructures import MultiDict

    params, errors = search.parse_search_params(
        MultiDict(
            dict(
                q="  rice   ",
                category="grocery",
                min_price="R10",
                max_price="50",
                radius="99",
                sort="highest",
                stores="a, b",
                page="2",
            )
        )
    )
    assert not errors
    assert (params.q, params.category, params.min_price, params.max_price) == (
        "rice",
        "Grocery",
        D("10.00"),
        D("50.00"),
    )
    assert (
        params.radius_km == app.config["SEARCH_MAX_RADIUS_KM"] and params.store_ids == ["a", "b"] and params.page == 2
    )

    for args, key in [
        ({"category": "Cars"}, "category"),
        ({"min_price": "abc"}, "min_price"),
        ({"max_price": "-1"}, "max_price"),
        ({"min_price": "50", "max_price": "10"}, "max_price"),
        ({"radius": "far"}, "radius"),
        ({"radius": "0"}, "radius"),
        ({"radius": "nan"}, "radius"),
        ({"sort": "random"}, "sort"),
        ({"page": "x"}, "page"),
        ({"q": "x" * 101}, "q"),
    ]:
        assert key in search.parse_search_params(MultiDict(args))[1], args


def test_api_search_returns_badged_cards_within_the_radius(signed_in, user, budget):
    data = signed_in.get("/api/search?q=milk&radius=5").get_json()
    assert data["total"] > 0 and data["radius_km"] == 5
    assert all(c["distance_km"] is None or c["distance_km"] <= 5 for c in data["results"])
    assert any("CHEAPEST" in c["badges"] for c in data["results"])
    card = data["results"][0]
    assert {"key", "barcode", "name", "price", "store_name", "distance_km", "badges", "in_stock", "in_list_qty"} <= set(
        card
    )


def test_search_without_query_or_category_is_empty_not_an_error(signed_in):
    data = signed_in.get("/api/search").get_json()
    assert data["total"] == 0 and data["results"] == []


def test_category_alone_lists_that_category(signed_in):
    data = signed_in.get("/api/search?category=Toiletries").get_json()
    assert data["total"] > 0 and {c["category"] for c in data["results"]} == {"Toiletries"}


def test_search_sorting(signed_in):
    def order(sort):
        return signed_in.get(f"/api/search?q=milk&sort={sort}").get_json()["results"]

    low = [D(c["price"]) for c in order("lowest")]
    assert low == sorted(low)
    high = [D(c["price"]) for c in order("highest")]
    assert high == sorted(high, reverse=True)
    near = [c["distance_km"] for c in order("closest")]
    assert near == sorted(near)
    names = [c["name"].casefold() for c in order("az")]
    assert names == sorted(names)
    stores = [c["store_name"].casefold() for c in order("store")]
    assert stores == sorted(stores)


def test_price_filter_is_inclusive(signed_in):
    everything = signed_in.get("/api/search?q=milk").get_json()["results"]
    prices = sorted({D(c["price"]) for c in everything})
    low, high = prices[1], prices[-2]
    shown = signed_in.get(f"/api/search?q=milk&min_price={low}&max_price={high}").get_json()["results"]
    assert shown and all(low <= D(c["price"]) <= high for c in shown)
    assert {D(c["price"]) for c in shown} >= {low, high}


def test_cheapest_badge_ignores_the_filters(signed_in, user):
    """A card must not claim CHEAPEST because a cheaper store was filtered out by price."""
    cards = signed_in.get("/api/search?q=chocolate+milk").get_json()["results"]
    same = sorted((c for c in cards if c["barcode"] == CHOC_MILK and c["in_stock"]), key=lambda c: D(c["price"]))
    assert len(same) >= 2
    filtered = signed_in.get(f"/api/search?q=chocolate+milk&min_price={same[1]['price']}").get_json()["results"]
    survivors = [c for c in filtered if c["barcode"] == CHOC_MILK]
    assert survivors and all("CHEAPEST" not in c["badges"] for c in survivors)


def test_store_filter(signed_in, app):
    upsert_stores(DURBAN_STORES)
    store = db.session.query(Store).filter(Store.Brand == "SPAR").first()
    data = signed_in.get(f"/api/search?q=milk&stores={store.StoreId}&radius=15").get_json()
    assert data["total"] > 0 and {c["store_id"] for c in data["results"]} == {store.Slug}


def test_search_paging(signed_in, app):
    app.config["SEARCH_PAGE_SIZE"] = 5
    first = signed_in.get("/api/search?q=milk&radius=15").get_json()
    assert first["pages"] > 1 and len(first["results"]) == 5
    second = signed_in.get("/api/search?q=milk&radius=15&page=2").get_json()
    assert second["page"] == 2 and {c["key"] for c in first["results"]}.isdisjoint(
        {c["key"] for c in second["results"]}
    )
    assert signed_in.get("/api/search?q=milk&radius=15&page=999").get_json()["page"] == first["pages"]


def test_search_marks_items_already_in_the_list(signed_in, user, budget, provider):
    offer = offers_for(provider, user)[0]
    signed_in.post("/api/list/items", json=add_body(offer))
    card = next(
        c
        for c in signed_in.get("/api/search?q=long+life+milk&radius=15").get_json()["results"]
        if c["barcode"] == MILK and c["store_id"] == offer.store_id
    )
    assert card["in_list_qty"] == 1


def test_searches_are_rate_limited_per_student(app, signed_in):
    app.config["RATELIMIT_ENABLED"] = True
    search_limiter.limit = 3
    search_limiter.clear()
    codes = [signed_in.get("/api/search?q=milk").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert signed_in.get("/api/search?q=milk").headers["Retry-After"]
    search_limiter.clear()


def test_provider_failure_is_a_502_not_a_traceback(app, signed_in, monkeypatch):
    from app.services.retail_api import RetailAPIError

    def boom(*args, **kwargs):
        raise RetailAPIError("down")

    monkeypatch.setattr(get_retail_provider().__class__, "search_products", boom)
    response = signed_in.get("/api/search?q=milk")
    assert response.status_code == 502 and "price service" in response.get_json()["error"]


def test_search_page_renders_with_and_without_a_budget(client, user, login):
    login(user)
    page = client.get("/search")
    assert page.status_code == 200 and b"no-budget-note" in page.data and b"budget-strip" not in page.data
    budgets.create_budget(user.UserId, "B", [("Grocery", D("100"))])
    page = client.get("/search")
    assert b"budget-strip" in page.data and b"Lowest price" in page.data and b"Store name" in page.data
    assert client.get("/search/").status_code in (200, 308)


# -------------------------------------------------------------------------------------------------- alternatives
def dearest_in_stock(provider, user, barcode=MILK):
    return [o for o in offers_for(provider, user, barcode) if o.in_stock][-1]


def test_same_product_cheaper_elsewhere_is_the_alternative(app, user, budget, provider):
    dear = dearest_in_stock(provider, user)
    cheapest = next(o for o in offers_for(provider, user) if o.in_stock)
    item = shopping.add_product(user, dear).item
    alt = shopping.find_alternative(provider, item, -29.8587, 31.0218)
    assert (
        alt and alt["same_product"] and D(alt["price"]) == cheapest.price and alt["store_name"] == cheapest.store_name
    )
    assert D(alt["saving"]) == dear.price - cheapest.price


def test_no_alternative_when_already_the_cheapest(app, user, budget, provider):
    item = shopping.add_product(user, next(o for o in offers_for(provider, user) if o.in_stock)).item
    alt = shopping.find_alternative(provider, item, -29.8587, 31.0218)
    assert alt is None or (not alt["same_product"] and D(alt["price"]) < item.UnitCost)


def test_refresh_caches_and_potential_savings_multiply_by_quantity(app, user, budget, provider):
    dear = dearest_in_stock(provider, user)
    item = shopping.add_product(user, dear).item
    shopping.set_quantity(user, item.ListItemId, 3)
    items = budgets.list_items(budgets.get_active_list(budget))
    shopping.refresh_alternatives(items, provider, user)
    assert item.CheaperAlternativeJSON["alternative"] is not None and "checked_at" in item.CheaperAlternativeJSON
    per_unit = D(item.CheaperAlternativeJSON["alternative"]["saving"])
    assert shopping.potential_savings(items) == per_unit * 3
    stamp = item.CheaperAlternativeJSON["checked_at"]
    shopping.refresh_alternatives(items, provider, user)  # fresh: not asked again
    assert item.CheaperAlternativeJSON["checked_at"] == stamp
    shopping.refresh_alternatives(items, provider, user, force=True)
    assert item.CheaperAlternativeJSON["checked_at"] != stamp


def test_replace_swaps_the_product_fields_and_remembers_the_old_one(app, user, budget, provider):
    dear = dearest_in_stock(provider, user)
    cheapest = next(o for o in offers_for(provider, user) if o.in_stock)
    item = shopping.add_product(user, dear).item
    shopping.set_quantity(user, item.ListItemId, 2)
    replaced, pos = shopping.replace_with_alternative(provider, user, item.ListItemId)
    assert replaced.ListItemId == item.ListItemId
    assert (replaced.UnitCost, replaced.StoreName, replaced.BarCode) == (
        cheapest.price,
        cheapest.store_name,
        cheapest.barcode,
    )
    assert replaced.ItemQuantity == 2
    old = replaced.CheaperAlternativeJSON["replaced"]
    assert (old["store_name"], D(old["price"]), old["barcode"]) == (dear.store_name, dear.price, dear.barcode)
    assert replaced.CheaperAlternativeJSON["alternative"] is None
    assert pos.in_list == cheapest.price * 2
    db.session.refresh(budget)
    assert budget.UsedAmount == cheapest.price * 2


def test_replace_merges_into_an_existing_row_at_the_target_store(app, user, budget, provider):
    dear = dearest_in_stock(provider, user)
    cheapest = next(o for o in offers_for(provider, user) if o.in_stock)
    dear_item = shopping.add_product(user, dear).item
    shopping.add_product(user, cheapest)
    result, pos = shopping.replace_with_alternative(provider, user, dear_item.ListItemId)
    rows = db.session.query(ListItem).filter_by(UserId=user.UserId).all()
    assert len(rows) == 1 and result.ItemQuantity == 2 and pos.in_list == cheapest.price * 2


def test_replace_when_nothing_cheaper_is_a_409(app, user, budget, provider):
    item = shopping.add_product(user, next(o for o in offers_for(provider, user) if o.in_stock)).item
    if shopping.find_alternative(provider, item, -29.8587, 31.0218):
        pytest.skip("mock catalogue has a similar cheaper product for this item")
    with pytest.raises(shopping.ShoppingError) as exc:
        shopping.replace_with_alternative(provider, user, item.ListItemId)
    assert exc.value.status == 409


def test_api_replace(signed_in, user, budget, provider):
    dear = dearest_in_stock(provider, user)
    item_id = signed_in.post("/api/list/items", json=add_body(dear)).get_json()["added"]["id"]
    response = signed_in.post(f"/api/list/items/{item_id}/replace")
    assert response.status_code == 200
    data = response.get_json()
    assert D(data["replaced"]["unit_cost"]) < dear.price and data["message"]
    assert signed_in.post(f"/api/list/items/{item_id}/replace").status_code == 409  # nothing cheaper left


# -------------------------------------------------------------------------------------------------- recommendations
def buy(user, provider, barcode, days_ago):
    """Record a finished trip that bought ``barcode`` ``days_ago`` days ago."""
    offer = next(o for o in offers_for(provider, user, barcode) if o.in_stock)
    budgets.create_budget(user.UserId, f"Trip {barcode}", [("Grocery", D("900"))])
    shopping.add_product(user, offer)
    budgets.complete_purchase(user.UserId)
    when = utcnow() - timedelta(days=days_ago)
    for item in db.session.query(ListItem).filter_by(UserId=user.UserId, BarCode=barcode):
        item.PurchasedDate = when
    db.session.commit()
    return offer


def test_purchase_tag_uses_the_recent_window(app):
    now = utcnow()
    assert recommendations.purchase_tag(now - timedelta(days=3), now) == "Recently purchased"
    assert recommendations.purchase_tag(now - timedelta(days=90), now) == "Lastly purchased"


def test_recommendations_for_a_new_student_are_only_deals(signed_in):
    results = signed_in.get("/api/recommendations").get_json()["results"]
    assert results and all(c["tag"] in ("Deal near you", "On promotion") for c in results)


def test_recommendations_list_past_purchases_with_their_tags_and_similar_items(signed_in, user, provider):
    buy(user, provider, MILK, 5)
    buy(user, provider, CHOC_MILK, 60)
    results = signed_in.get("/api/recommendations").get_json()["results"]
    tags = {c["barcode"]: c["tag"] for c in results if c["barcode"] in (MILK, CHOC_MILK)}
    assert tags == {MILK: "Recently purchased", CHOC_MILK: "Lastly purchased"}
    assert len({c["key"] for c in results}) == len(results)  # no repeats
    assert all(c["tag"] for c in results)
    bought = next(c for c in results if c["barcode"] == MILK)
    cheapest = next(o for o in offers_for(provider, user) if o.in_stock)
    assert D(bought["price"]) == cheapest.price  # today's cheapest, not last time's price


def test_a_bought_product_earns_the_recommended_badge_in_search(signed_in, user, provider):
    buy(user, provider, MILK, 5)
    cards = signed_in.get("/api/search?q=long+life+milk&radius=15").get_json()["results"]
    assert any(c["barcode"] == MILK and "RECOMMENDED" in c["badges"] for c in cards)


def test_only_purchased_items_count_as_history(app, user, budget, provider):
    shopping.add_product(user, offers_for(provider, user)[0])  # on the list, not bought
    assert recommendations.purchase_history(user.UserId) == []


@pytest.mark.parametrize("bad", ["²", "３", "١", "1e3", "-1", "1.5", " ", "", "9" * 12, "0"])
def test_odd_quantity_text_is_a_400_not_a_crash(signed_in, user, budget, provider, bad):
    item_id = signed_in.post("/api/list/items", json=add_body(offers_for(provider, user)[0])).get_json()["added"]["id"]
    response = signed_in.patch(f"/api/list/items/{item_id}", json={"quantity": bad})
    assert response.status_code == 400 and response.get_json()["error"]


# -------------------------------------------------------------------------------------------------- review fixes
def _repriced(offer, price):
    from dataclasses import replace

    return replace(offer, price=D(str(price)))


def test_adding_again_puts_every_unit_at_todays_price(app, user, budget, provider):
    offer = offers_for(provider, user)[0]
    shopping.add_product(user, offer)
    result = shopping.add_product(user, _repriced(offer, offer.price + 5))
    assert result.item.ItemQuantity == 2 and result.item.UnitCost == offer.price + 5
    assert budgets.get_active_budget(user.UserId).UsedAmount == (offer.price + 5) * 2


def test_reorder_merge_reprices_rows_already_on_the_list(app, user, budget, provider):
    offer = offers_for(provider, user)[0]
    shopping.add_product(user, offer)
    position = shopping.add_offers(user, [(_repriced(offer, offer.price + 2), 2)])
    item = db.session.query(ListItem).one()
    assert item.ItemQuantity == 3 and item.UnitCost == offer.price + 2
    assert position.used == (offer.price + 2) * 3


def test_a_merge_past_the_quantity_cap_is_refused_and_changes_nothing(app, user, budget, provider):
    offer = offers_for(provider, user)[0]
    shopping.add_product(user, offer)
    cap = app.config["MAX_ITEM_QUANTITY"]
    item = db.session.query(ListItem).one()
    shopping.set_quantity(user, item.ListItemId, cap - 1)
    before = budgets.get_active_budget(user.UserId).UsedAmount
    with pytest.raises(shopping.ShoppingError) as raised:
        shopping.add_offers(user, [(offer, 2)])
    assert raised.value.status == 409
    assert db.session.query(ListItem).one().ItemQuantity == cap - 1
    assert budgets.get_active_budget(user.UserId).UsedAmount == before


def test_replace_that_would_merge_past_the_cap_is_refused(app, user, budget, provider):
    offers = [o for o in offers_for(provider, user) if o.in_stock]
    dearest, cheapest = offers[-1], offers[0]
    first = shopping.add_product(user, dearest).item
    second = shopping.add_product(user, cheapest).item
    cap = app.config["MAX_ITEM_QUANTITY"]
    shopping.set_quantity(user, first.ListItemId, 5)
    shopping.set_quantity(user, second.ListItemId, cap - 1)
    with pytest.raises(shopping.ShoppingError):
        shopping.replace_with_alternative(provider, user, first.ListItemId)
    assert sorted(i.ItemQuantity for i in db.session.query(ListItem)) == [5, cap - 1]


def test_alternative_saving_uses_the_same_rounding_as_replace(app, user, provider):
    offer = _repriced(offers_for(provider, user)[0], "29.995")
    info = shopping._alt_dict(offer, D("31.00"), same_product=True)
    assert info["price"] == "30.00" and info["saving"] == "1.00"


@pytest.mark.parametrize(
    "body", [[1, 2], "text", 5, None, {"barcode": 123, "store_id": "x"}, {"name": ["a"], "store_name": {}}]
)
def test_odd_json_bodies_are_400s_not_500s(signed_in, user, budget, body):
    assert signed_in.post("/api/list/items", json=body).status_code == 400
    assert signed_in.patch("/api/list", json=body).status_code == 400
    assert signed_in.patch("/api/list/items/ITM-x", json=body).status_code == 400


@pytest.mark.parametrize(
    "entries",
    [
        [("Combined", D("100")), ("Grocery", D("50"))],
        [("Grocery", D("50")), ("Grocery", D("60"))],
        [("Grocery", D("-5"))],
        [("Grocery", D("0"))],
    ],
)
def test_create_budget_refuses_bad_entries_even_when_called_directly(app, user, entries):
    with pytest.raises(budgets.BudgetError):
        budgets.create_budget(user.UserId, "Bad", entries)
    assert budgets.get_active_budget(user.UserId) is None


# ---------------------------------------------------------------------------------------- factory scenarios (Step 20)
# ProductFactory offers through a scripted provider make the prices exact, so these read as the rules themselves.
from app.services.retail_api import MockProvider, RetailProvider, group_by_barcode  # noqa: E402
from tests.factories import BudgetFactory, ListItemFactory, ProductFactory, UserFactory  # noqa: E402

SHARED = "6001234567890"


class ScriptedProvider(RetailProvider):
    """Returns exactly the offers it was given (a hand-made stand-in for the retail API)."""

    name = "scripted"

    def __init__(self, *offers):
        self.offers = list(offers)

    def search_products(self, query, lat, lng, radius_km=15, category=None):
        return [o for o in self.offers if not category or o.category == category]

    def get_offers_by_barcode(self, barcode, lat=None, lng=None, radius_km=15):
        return sorted((o for o in self.offers if o.barcode == barcode), key=lambda o: o.price)

    def get_price_history(self, barcode, store_id):
        return []


def three_stores(barcode=SHARED):
    return (
        ProductFactory(
            barcode=barcode, name="Clover Milk 1L", price=D("24.99"), store_name="Shoprite Pinetown", store_id="sr-pine"
        ),
        ProductFactory(
            barcode=barcode, name="Clover Milk 1L", price=D("21.99"), store_name="Checkers Gateway", store_id="ck-gate"
        ),
        ProductFactory(
            barcode=barcode,
            name="Clover Milk 1L",
            price=D("22.99"),
            store_name="Pick n Pay Musgrave",
            store_id="pnp-mus",
        ),
    )


def test_scenario_same_barcode_is_grouped_and_ordered_cheapest_first():
    other = ProductFactory(barcode="6009999999999", name="Sasko Bread", price=D("16.99"))
    groups = group_by_barcode([*three_stores(), other])
    assert [g.barcode for g in groups] == [SHARED, "6009999999999"]
    milk = groups[0]
    assert [o.price for o in milk.offers] == [D("21.99"), D("22.99"), D("24.99")]
    assert milk.cheapest.store_name == "Checkers Gateway" and milk.savings == D("3.00")


def test_scenario_an_out_of_stock_offer_is_never_the_cheapest():
    offers = (
        *three_stores(),
        ProductFactory(barcode=SHARED, price=D("9.99"), store_name="Boxer Umlazi", in_stock=False),
    )
    group = group_by_barcode(offers)[0]
    assert group.cheapest.store_name == "Checkers Gateway"
    assert group.savings == D("3.00")


def test_scenario_products_without_a_barcode_are_never_merged():
    a = ProductFactory(barcode=None, name="Loose apples", price=D("10.00"))
    b = ProductFactory(barcode=None, name="Loose apples", price=D("12.00"), store_name="Boxer Umlazi")
    assert len(group_by_barcode([a, b])) == 2


def test_scenario_mock_catalogue_groups_are_consistent(app):
    """MockProvider: every group shares one barcode and is priced in rands, cheapest first."""
    provider = MockProvider()
    offers = provider.search_products("milk", -29.8587, 31.0218, 15)
    assert offers
    for group in group_by_barcode(offers):
        assert all(o.barcode == group.barcode for o in group.offers)
        prices = [o.price for o in group.offers]
        assert prices == sorted(prices) and all(isinstance(p, Decimal) and p > 0 for p in prices)


def test_scenario_adding_the_same_barcode_at_the_same_store_adds_a_unit(app):
    user = UserFactory()
    budget = BudgetFactory(user=user)
    _, checkers, pnp = three_stores()
    shopping.add_product(user, checkers)
    again = shopping.add_product(user, checkers, quantity=2)
    assert not again.created and again.item.ItemQuantity == 3
    other_store = shopping.add_product(user, pnp)
    assert other_store.created
    assert db.session.query(ListItem).filter_by(UserId=user.UserId).count() == 2  # same barcode, two stores, two rows
    db.session.refresh(budget)
    assert budget.UsedAmount == checkers.price * 3 + pnp.price


def test_scenario_adding_over_budget_is_allowed_and_carries_the_alert(app):
    user = UserFactory()
    BudgetFactory(user=user, entries=[("Grocery", D("30"))])
    result = shopping.add_product(user, ProductFactory(price=D("29.99")))
    assert not result.position.is_over and result.alert is None
    result = shopping.add_product(user, ProductFactory(price=D("29.99")))  # R59.98 of R30: still added
    assert result.created and result.position.is_over and result.alert


def test_scenario_replace_with_the_cheaper_store_keeps_quantity_and_saves_money(app):
    user = UserFactory(Latitude=-29.8587, Longitude=31.0218)
    budget = BudgetFactory(user=user)
    dear = three_stores()[0]
    provider = ScriptedProvider(*three_stores())
    item = ListItemFactory(
        budget=budget, name=dear.name, price=str(dear.price), qty=2, barcode=SHARED, store=dear.store_name
    )
    alt = shopping.find_alternative(provider, item, -29.8587, 31.0218)
    assert alt["same_product"] and alt["store_name"] == "Checkers Gateway" and D(alt["saving"]) == D("3.00")
    replaced, position = shopping.replace_with_alternative(provider, user, item.ListItemId)
    assert (replaced.StoreName, replaced.UnitCost, replaced.ItemQuantity) == ("Checkers Gateway", D("21.99"), 2)
    assert position.in_list == D("43.98")


def test_scenario_replace_is_refused_when_nothing_is_cheaper(app):
    user = UserFactory(Latitude=-29.8587, Longitude=31.0218)
    budget = BudgetFactory(user=user)
    cheap = three_stores()[1]
    item = ListItemFactory(
        budget=budget, name=cheap.name, price=str(cheap.price), barcode=SHARED, store=cheap.store_name
    )
    with pytest.raises(shopping.ShoppingError) as raised:
        shopping.replace_with_alternative(ScriptedProvider(*three_stores()), user, item.ListItemId)
    assert raised.value.status == 409
