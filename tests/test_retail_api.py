import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
import responses

from app.data.mock_products import CATALOGUE, ean13_check_digit
from app.extensions import cache
from app.services import http
from app.services.retail_api import (
    ApifyProvider,
    CachedProvider,
    MockProvider,
    ParseBotProvider,
    Product,
    RetailAPIError,
    RetailConfigError,
    RetailProvider,
    _fill,
    build_provider,
    clean_barcode,
    get_retail_provider,
    group_by_barcode,
    normalise_product,
    parse_price,
)

DURBAN = (-29.8587, 31.0218)
UMHLANGA = (-29.7268, 31.0700)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)


@pytest.fixture
def mock():
    return MockProvider()


# ------------------------------------------------------------------------------ catalogue
def test_catalogue_has_about_two_hundred_realistic_products():
    assert len(CATALOGUE) >= 200
    names = " ".join(item.name for item in CATALOGUE)
    for brand in ("Ariel", "Albany", "Clover", "Nulaid", "Sunlight", "Colgate", "Dettol"):
        assert brand in names
    assert {item.category for item in CATALOGUE} == {"Grocery", "Toiletries", "Clothes"}


def test_catalogue_barcodes_are_unique_valid_ean13_in_the_reserved_range():
    codes = [item.barcode for item in CATALOGUE]
    assert len(set(codes)) == len(codes)
    for code in codes:
        assert len(code) == 13 and code.startswith("2")
        assert code[-1] == ean13_check_digit(code[:12])


def test_barcodes_are_stable_across_runs():
    assert CATALOGUE[0].barcode == "2000000000015"  # position 1: never changes if rows are only appended


def test_catalogue_prices_are_sensible_rands():
    for item in CATALOGUE:
        assert Decimal("1") < Decimal(item.price) < Decimal("500"), item


# ------------------------------------------------------------------------------ mock provider
def test_search_returns_normalised_products_from_several_stores(app, mock):
    results = mock.search_products("albany", *DURBAN)
    assert results and all(isinstance(p, Product) for p in results)
    product = results[0]
    assert "Albany" in product.name and product.barcode and product.barcode.startswith("2")
    assert isinstance(product.price, Decimal) and product.price > 0 and str(product.price).endswith(".99")
    assert product.store_name and product.store_address and product.store_lat and product.store_lng
    assert (
        product.image_url.startswith("/static/img/placeholders/")
        and product.category == "Grocery"
        and product.in_stock in (True, False)
    )
    assert len({p.retailer for p in results}) >= 3  # comparison needs several stores


def test_search_is_deterministic(mock):
    assert mock.search_products("milk", *DURBAN) == mock.search_products("milk", *DURBAN)


@pytest.mark.parametrize(
    "query, expected",
    [("Ariel", "Ariel"), ("full cream milk", "Full Cream"), ("COLGATE", "Colgate"), ("dettol", "Dettol")],
)
def test_search_finds_the_examples_from_the_brief(mock, query, expected):
    assert any(expected in p.name for p in mock.search_products(query, *DURBAN))


def test_search_requires_every_word(mock):
    assert all("Milk" in p.name and "Full Cream" in p.name for p in mock.search_products("full cream milk", *DURBAN))
    assert mock.search_products("zzzz nothing", *DURBAN) == []


def test_search_defaults_to_browsing_the_catalogue_and_includes_clothes(mock):
    results = mock.search_products("", *DURBAN)
    whitespace_results = mock.search_products("   ", *DURBAN)
    assert results
    assert whitespace_results
    assert len(results) >= 20
    assert {p.category for p in results} >= {"Clothes"}
    assert {p.category for p in whitespace_results} >= {"Clothes"}
    assert {p.category for p in mock.search_products("", *DURBAN, category="Toiletries")} == {"Toiletries"}
    assert mock.search_products("", *DURBAN, category="Electronics") == []  # no longer a category


def test_category_filter_is_case_insensitive_and_strict(mock):
    assert mock.search_products("soap", *DURBAN, category="grocery") == []
    assert {p.category for p in mock.search_products("soap", *DURBAN, category="TOILETRIES")} == {"Toiletries"}
    assert mock.search_products("milk", *DURBAN, category="Nonsense") == []


def test_offers_for_one_product_are_cheapest_first(mock):
    offers = mock.search_products("Ariel Auto Washing Powder 2kg", *DURBAN)
    assert len(offers) >= 3 and {o.barcode for o in offers} == {offers[0].barcode}
    assert [o.price for o in offers] == sorted(o.price for o in offers)


def test_woolworths_is_dearer_than_shoprite_on_average(mock):
    totals = {"Woolworths": Decimal(0), "Shoprite": Decimal(0)}
    counted = {"Woolworths": 0, "Shoprite": 0}
    for item in CATALOGUE:
        for offer in mock.get_offers_by_barcode(item.barcode, *DURBAN, radius_km=30):
            if offer.retailer in totals:
                totals[offer.retailer] += offer.price / Decimal(item.price)
                counted[offer.retailer] += 1
    assert totals["Woolworths"] / counted["Woolworths"] > totals["Shoprite"] / counted["Shoprite"]


def test_radius_limits_which_stores_are_used(mock):
    near = mock.search_products("albany", *UMHLANGA, radius_km=2)
    assert near and all(p.distance_km <= 2 for p in near)
    assert mock.search_products("albany", -33.9, 18.4, radius_km=15) == []  # Cape Town


def test_results_carry_the_distance_to_their_store(mock):
    results = mock.search_products("albany", *UMHLANGA, radius_km=15)
    for product in results:
        assert product.distance_km is not None and product.distance_km <= 15


def test_get_product_by_barcode_returns_the_cheapest_available_offer(mock):
    barcode = mock.search_products("Clover Full Cream Fresh Milk 2L", *DURBAN)[0].barcode
    offers = mock.get_offers_by_barcode(barcode, *DURBAN)
    best = mock.get_product_by_barcode(barcode, *DURBAN)
    in_stock = [o for o in offers if o.in_stock]
    assert best.price == min(o.price for o in (in_stock or offers))
    assert mock.get_product_by_barcode("9999999999999") is None
    assert mock.get_product_by_barcode("") is None


def test_barcode_lookup_defaults_to_durban(mock):
    assert mock.get_offers_by_barcode(CATALOGUE[0].barcode)


def test_get_stores_near_is_sorted_and_within_radius(mock):
    stores = mock.get_stores_near(*UMHLANGA, radius_km=5)
    assert stores and [s.distance_km for s in stores] == sorted(s.distance_km for s in stores)
    assert all(s.distance_km <= 5 for s in stores)
    assert mock.get_stores_near(-33.9, 18.4, 15) == []


def test_price_history_is_twelve_weekly_points_ending_at_todays_price(mock):
    offer = mock.search_products("Ariel Auto Washing Powder 2kg", *DURBAN)[0]
    history = mock.get_price_history(offer.barcode, offer.store_id)
    assert len(history) == 12
    assert [p.date for p in history] == sorted(p.date for p in history)
    assert history[-1].date == date.today() and history[0].date == date.today() - timedelta(weeks=11)
    assert history[-1].price == offer.price
    assert all(p.price > 0 and str(p.price).endswith(".99") for p in history)
    assert history == mock.get_price_history(offer.barcode, offer.store_id)  # deterministic
    assert mock.get_price_history(offer.barcode, "no-such-store") == []
    assert mock.get_price_history("nope", offer.store_id) == []


def test_product_to_dict_is_json_safe(mock):
    data = mock.search_products("albany", *DURBAN)[0].to_dict()
    assert json.loads(json.dumps(data))["price"].count(".") == 1
    assert set(data) >= {
        "name",
        "barcode",
        "price",
        "image_url",
        "store_name",
        "store_address",
        "store_lat",
        "store_lng",
        "category",
        "in_stock",
    }


# ------------------------------------------------------------------------------ barcode grouping
def _product(name, price, barcode, store="Store A", stock=True):
    return Product(name, barcode, Decimal(price), None, store, None, None, None, "Grocery", stock)


def test_same_barcode_is_grouped_and_sorted_cheapest_first():
    groups = group_by_barcode(
        [
            _product("Milk 2L", "39.99", "111", "PnP"),
            _product("Bread", "17.99", "222", "PnP"),
            _product("Clover Milk 2L", "36.99", "111", "Shoprite"),
            _product("Milk 2L", "42.99", "111", "Woolworths"),
            _product("Loose apples", "10.00", None, "SPAR"),
        ]
    )
    assert [g.barcode for g in groups] == ["111", "222", None]
    milk = groups[0]
    assert [o.store_name for o in milk.offers] == ["Shoprite", "PnP", "Woolworths"]
    assert milk.cheapest.store_name == "Shoprite" and milk.savings == Decimal("6.00")
    assert groups[2].offers[0].name == "Loose apples"


def test_products_without_barcodes_are_never_merged():
    groups = group_by_barcode([_product("Apples", "10", None), _product("Apples", "12", None)])
    assert len(groups) == 2


def test_cheapest_ignores_out_of_stock_offers_when_possible():
    group = group_by_barcode([_product("X", "10", "1", "A", stock=False), _product("X", "12", "1", "B")])[0]
    assert group.cheapest.store_name == "B"
    only_oos = group_by_barcode([_product("X", "10", "1", "A", stock=False)])[0]
    assert only_oos.cheapest.store_name == "A" and only_oos.savings == Decimal("0.00")


def test_mock_search_groups_by_barcode(mock):
    groups = group_by_barcode(mock.search_products("Ariel Auto Washing Powder 2kg", *DURBAN))
    assert len(groups) == 1 and len(groups[0].offers) >= 3


# ------------------------------------------------------------------------------ parsing helpers
@pytest.mark.parametrize(
    "raw, expected",
    [
        (29.99, "29.99"),
        (30, "30.00"),
        ("R29.99", "29.99"),
        ("R 1 049,00", "1049.00"),
        ("R1,049.00", "1049.00"),
        ("1.049,50", "1049.50"),
        ("29,99", "29.99"),
        ({"amount": 12.5}, "12.50"),
        ({"current": "R9,99"}, "9.99"),
        ([5.5], "5.50"),
        (Decimal("7.005"), "7.01"),
    ],
)
def test_parse_price(raw, expected):
    assert parse_price(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [None, "", "free", 0, -5, "R0.00", True, {}, float("nan"), "abc"])
def test_parse_price_rejects_unusable_values(raw):
    assert parse_price(raw) is None


def test_parse_price_in_cents():
    assert parse_price(2999, in_cents=True) == Decimal("29.99")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("6001234567890", "6001234567890"),
        (6001234567890, "6001234567890"),
        ("600 123 4567 890", "6001234567890"),
        ("12345678", "12345678"),
        ("SKU-12345", None),
        ("123", None),
        (None, None),
        ("", None),
    ],
)
def test_clean_barcode_only_accepts_real_gtin_lengths(raw, expected):
    assert clean_barcode(raw) == expected


def test_normalise_product_maps_common_field_names():
    product = normalise_product(
        {
            "title": "Clover Full Cream Milk 2L",
            "currentPrice": "R38.99",
            "ean": "6001234567890",
            "imageUrl": "https://img/x.png",
            "url": "https://shop/x",
            "category": ["Dairy", "Milk"],
            "inStock": False,
            "brand": "Clover",
        },
        retailer="Checkers",
    )
    assert (product.name, product.price, product.barcode) == (
        "Clover Full Cream Milk 2L",
        Decimal("38.99"),
        "6001234567890",
    )
    assert product.image_url == "https://img/x.png" and not hasattr(product, "store_link")
    assert product.category == "Milk" and product.in_stock is False and product.retailer == "Checkers"


def test_normalise_product_honours_a_field_map_and_nested_paths():
    product = normalise_product(
        {"info": {"label": "Rice 2kg"}, "pricing": {"now": 4499}, "images": ["https://cdn.example/a.png", "b.png"]},
        retailer="Shoprite",
        field_map={"name": "info.label", "price": "pricing.now"},
        price_in_cents=True,
    )
    assert (
        product.name == "Rice 2kg"
        and product.price == Decimal("44.99")
        and product.image_url == "https://cdn.example/a.png"
    )


@pytest.mark.parametrize("raw", [{"name": "No price"}, {"price": 5}, {"name": "Zero", "price": 0}, "text", None, []])
def test_normalise_product_skips_unusable_rows(raw):
    assert normalise_product(raw, retailer="Checkers") is None


@pytest.mark.parametrize(
    "value, expected", [("false", False), ("Out of Stock", False), ("yes", True), (True, True), (0, False)]
)
def test_stock_flags_are_understood(value, expected):
    assert normalise_product({"name": "x", "price": 1, "available": value}, retailer="SPAR").in_stock is expected


def test_fill_replaces_placeholders_and_keeps_types():
    filled = _fill(
        {"search": "{query}", "loc": {"lat": "{lat}", "text": "near {query}"}, "n": 3, "tags": ["{category}"]},
        {"query": "milk", "lat": -29.8, "category": "Grocery"},
    )
    assert filled == {"search": "milk", "loc": {"lat": -29.8, "text": "near milk"}, "n": 3, "tags": ["Grocery"]}


# ------------------------------------------------------------------------------ Apify
APIFY = "https://api.apify.com/v2"
ACTORS = {"checkers": {"actor": "someone/checkers-scraper", "input": {"search": "{query}", "maxItems": 50}}}


def _apify(**kwargs):
    return ApifyProvider("token-123", kwargs.pop("actors", ACTORS), sleep=lambda _s: None, **kwargs)


def _run(status="SUCCEEDED", run_id="run1", dataset="ds1"):
    return {"data": {"id": run_id, "status": status, "defaultDatasetId": dataset}}


def _items(n, start=0):
    return [{"name": f"Item {i}", "price": f"R{10 + i}.99", "ean": f"60012345{i:05d}"} for i in range(start, start + n)]


@responses.activate
def test_apify_runs_the_actor_and_reads_the_dataset(app):
    responses.post(f"{APIFY}/acts/someone~checkers-scraper/runs", json=_run())
    responses.get(f"{APIFY}/datasets/ds1/items", json=_items(3))
    products = _apify().search_products("milk", *DURBAN)
    assert len(products) == 3 and {p.retailer for p in products} == {"Checkers"}
    start = responses.calls[0].request
    assert start.headers["Authorization"] == "Bearer token-123"
    assert json.loads(start.body) == {"search": "milk", "maxItems": 50}
    assert products[0].price == Decimal("10.99") and products[0].barcode == "6001234500000"


@responses.activate
def test_apify_polls_until_the_run_finishes(app):
    responses.post(f"{APIFY}/acts/someone~checkers-scraper/runs", json=_run("RUNNING"))
    responses.get(f"{APIFY}/actor-runs/run1", json=_run("RUNNING"))
    responses.get(f"{APIFY}/actor-runs/run1", json=_run("SUCCEEDED"))
    responses.get(f"{APIFY}/datasets/ds1/items", json=_items(1))
    assert len(_apify().search_products("milk", *DURBAN)) == 1
    assert sum("actor-runs" in c.request.url for c in responses.calls) == 2


@responses.activate
def test_apify_paginates_the_dataset(app):
    responses.post(f"{APIFY}/acts/someone~checkers-scraper/runs", json=_run())
    responses.get(f"{APIFY}/datasets/ds1/items", json=_items(5, 0))
    responses.get(f"{APIFY}/datasets/ds1/items", json=_items(5, 5))
    responses.get(f"{APIFY}/datasets/ds1/items", json=_items(2, 10))
    products = _apify(page_size=5, max_items=100).search_products("x", *DURBAN)
    assert len(products) == 12
    pages = [c.request.url for c in responses.calls if "datasets" in c.request.url]
    assert [("offset=%d" % o) in u for o, u in zip((0, 5, 10), pages)] == [True] * 3 and "limit=5" in pages[0]


@responses.activate
def test_apify_stops_at_max_items(app):
    responses.post(f"{APIFY}/acts/someone~checkers-scraper/runs", json=_run())
    responses.get(f"{APIFY}/datasets/ds1/items", json=_items(4))
    responses.get(f"{APIFY}/datasets/ds1/items", json=_items(4, 4))
    assert len(_apify(page_size=4, max_items=6).search_products("x", *DURBAN)) == 6
    assert len([c for c in responses.calls if "datasets" in c.request.url]) == 2


@responses.activate
def test_apify_retries_429_and_5xx_and_logs_every_call(app):
    http.init_api_logging(app)
    open(app.config["API_LOG_FILE"], "w").close()
    responses.post(f"{APIFY}/acts/someone~checkers-scraper/runs", status=429, headers={"Retry-After": "1"})
    responses.post(f"{APIFY}/acts/someone~checkers-scraper/runs", json=_run())
    responses.get(f"{APIFY}/datasets/ds1/items", status=503)
    responses.get(f"{APIFY}/datasets/ds1/items", json=_items(2))
    assert len(_apify().search_products("milk", *DURBAN)) == 2
    for handler in http.logging.getLogger(http.API_LOGGER_NAME).handlers:
        handler.flush()
    lines = [json.loads(line) for line in open(app.config["API_LOG_FILE"]) if line.strip()]
    assert [(entry["service"], entry["status"]) for entry in lines] == [
        ("apify", 429),
        ("apify", 200),
        ("apify", 503),
        ("apify", 200),
    ]
    assert "token-123" not in open(app.config["API_LOG_FILE"]).read()


@responses.activate
def test_apify_bad_token_is_a_clear_error(app):
    responses.post(f"{APIFY}/acts/someone~checkers-scraper/runs", status=401)
    with pytest.raises(RetailAPIError, match="rejected the API token"):
        _apify().search_products("milk", *DURBAN)


@responses.activate
def test_apify_failed_run_is_an_error_not_an_empty_result(app):
    responses.post(f"{APIFY}/acts/someone~checkers-scraper/runs", json=_run("FAILED"))
    with pytest.raises(RetailAPIError, match="FAILED"):
        _apify().search_products("milk", *DURBAN)


@responses.activate
def test_apify_run_that_never_finishes_times_out(app):
    clock = iter(range(0, 1000, 60))
    provider = ApifyProvider("t", ACTORS, run_timeout=100, sleep=lambda _s: None, clock=lambda: next(clock))
    responses.post(f"{APIFY}/acts/someone~checkers-scraper/runs", json=_run("RUNNING"))
    responses.get(f"{APIFY}/actor-runs/run1", json=_run("RUNNING"))
    with pytest.raises(RetailAPIError, match="did not finish"):
        provider.search_products("milk", *DURBAN)


@responses.activate
def test_apify_one_failing_retailer_does_not_hide_the_others(app):
    actors = {"checkers": {"actor": "a/checkers"}, "shoprite": {"actor": "b/shoprite"}}
    responses.post(f"{APIFY}/acts/a~checkers/runs", json=_run("FAILED"))
    responses.post(f"{APIFY}/acts/b~shoprite/runs", json=_run(dataset="ds2"))
    responses.get(f"{APIFY}/datasets/ds2/items", json=_items(2))
    products = _apify(actors=actors).search_products("milk", *DURBAN)
    assert {p.retailer for p in products} == {"Shoprite"}


@responses.activate
def test_apify_products_are_pinned_to_the_nearest_branch(app):
    from app.cli import upsert_stores
    from app.data.durban_stores import DURBAN_STORES

    upsert_stores(DURBAN_STORES)
    responses.post(f"{APIFY}/acts/someone~checkers-scraper/runs", json=_run())
    responses.get(f"{APIFY}/datasets/ds1/items", json=_items(1))
    product = _apify().search_products("milk", *UMHLANGA, radius_km=15)[0]
    assert product.store_name.startswith("Checkers") and product.store_address and product.store_lat
    assert product.store_id.startswith("STR-") and product.distance_km is not None


@responses.activate
def test_apify_out_of_range_retailer_keeps_the_brand_name_only(app):
    responses.post(f"{APIFY}/acts/someone~checkers-scraper/runs", json=_run())
    responses.get(f"{APIFY}/datasets/ds1/items", json=_items(1))
    product = _apify().search_products("milk", -33.9, 18.4, radius_km=5)[0]  # no seeded stores in Cape Town
    assert product.store_name == "Checkers" and product.distance_km is None


@responses.activate
def test_apify_barcode_lookup_searches_for_the_number_and_keeps_exact_matches(app):
    responses.post(f"{APIFY}/acts/someone~checkers-scraper/runs", json=_run())
    responses.get(
        f"{APIFY}/datasets/ds1/items",
        json=[
            {"name": "Right one", "price": 10, "ean": "6001234567890"},
            {"name": "Other", "price": 5, "ean": "6009999999999"},
        ],
    )
    offers = _apify().get_offers_by_barcode("6001234567890", *DURBAN)
    assert [o.name for o in offers] == ["Right one"]
    assert _apify().get_product_by_barcode("not-a-barcode") is None


def test_apify_configuration_is_validated():
    with pytest.raises(RetailConfigError, match="APIFY_API_TOKEN"):
        ApifyProvider("", ACTORS)
    with pytest.raises(RetailConfigError, match="empty"):
        ApifyProvider("t", {})
    with pytest.raises(RetailConfigError, match="known retailer"):
        ApifyProvider("t", {"tesco": {"actor": "x/y"}})
    with pytest.raises(RetailConfigError, match="'actor'"):
        ApifyProvider("t", {"checkers": {"input": {}}})


def test_apify_never_calls_the_network_for_an_empty_search(app):
    with responses.RequestsMock() as rsps:  # any request would fail the test
        assert _apify().search_products("", *DURBAN) == []
        assert len(rsps.calls) == 0


def test_apify_history_is_not_available(app):
    assert _apify().get_price_history("6001234567890", "store") == []


# ------------------------------------------------------------------------------ Parse.bot
PB = "https://api.parse.bot/scraper/3c08f360-415f-4984-bcd1-846bc0d7dafc/search_products"


def _pb_item(i):
    return {"name": f"Woolies item {i}", "price": f"R{20 + i}.00", "url": f"https://woolworths.co.za/p/{i}"}


@responses.activate
def test_parsebot_searches_woolworths_with_the_api_key_header(app):
    responses.get(PB, json={"products": [_pb_item(1), _pb_item(2)], "total": 2})
    products = ParseBotProvider("pb-key").search_products("milk", *DURBAN)
    assert len(products) == 2 and {p.retailer for p in products} == {"Woolworths"}
    request = responses.calls[0].request
    assert request.headers["X-API-Key"] == "pb-key" and "query=milk" in request.url and "offset=0" in request.url


@responses.activate
def test_parsebot_paginates_with_offset_until_total(app):
    responses.get(PB, json={"products": [_pb_item(i) for i in range(3)], "total": 5})
    responses.get(PB, json={"products": [_pb_item(i) for i in range(3, 5)], "total": 5})
    assert len(ParseBotProvider("k").search_products("milk", *DURBAN)) == 5
    assert "offset=3" in responses.calls[1].request.url and len(responses.calls) == 2


@responses.activate
def test_parsebot_stops_on_an_empty_page(app):
    responses.get(PB, json={"products": [_pb_item(1)]})
    responses.get(PB, json={"products": []})
    assert len(ParseBotProvider("k").search_products("milk", *DURBAN)) == 1


@responses.activate
def test_parsebot_error_statuses_are_explained(app):
    for status, text in ((401, "rejected"), (402, "credits"), (404, "HTTP 404")):
        responses.upsert(responses.GET, PB, status=status)
        with pytest.raises(RetailAPIError, match=text):
            ParseBotProvider("k").search_products("milk", *DURBAN)


@responses.activate
def test_parsebot_rate_limits_are_retried(app):
    responses.get(PB, status=429)
    responses.get(PB, json={"products": [_pb_item(1)], "total": 1})
    assert len(ParseBotProvider("k").search_products("milk", *DURBAN)) == 1


@responses.activate
def test_parsebot_extra_scrapers_come_from_config(app):
    responses.get("https://api.parse.bot/scraper/pnp-id/search_products", json={"items": [_pb_item(9)]})
    responses.get(PB, json={"products": [_pb_item(1)]})
    provider = ParseBotProvider("k", {"pnp": "pnp-id"})
    assert {p.retailer for p in provider.search_products("milk", *DURBAN)} == {"Woolworths", "Pick n Pay"}


def test_parsebot_configuration_is_validated():
    with pytest.raises(RetailConfigError, match="PARSEBOT_API_KEY"):
        ParseBotProvider("")
    with pytest.raises(RetailConfigError, match="known retailer"):
        ParseBotProvider("k", {"tesco": "x"})


# ------------------------------------------------------------------------------ factory + cache
def test_default_provider_is_the_mock(app):
    assert isinstance(build_provider(app.config), MockProvider)
    provider = get_retail_provider()
    assert isinstance(provider, CachedProvider) and provider.name == "mock"
    assert get_retail_provider() is provider  # built once per app


def test_provider_choice_and_errors(app):
    app.config.update(RETAIL_PROVIDER="apify", APIFY_API_TOKEN=None)
    with pytest.raises(RetailConfigError):
        build_provider(app.config)
    app.config.update(APIFY_API_TOKEN="t", APIFY_ACTORS_JSON=json.dumps(ACTORS))
    assert isinstance(build_provider(app.config), ApifyProvider)
    app.config["APIFY_ACTORS_JSON"] = "{not json"
    with pytest.raises(RetailConfigError, match="not valid JSON"):
        build_provider(app.config)
    app.config.update(RETAIL_PROVIDER="parsebot", PARSEBOT_API_KEY="k", PARSEBOT_SCRAPERS_JSON=None)
    assert isinstance(build_provider(app.config), ParseBotProvider)
    app.config["RETAIL_PROVIDER"] = "carrier-pigeon"
    with pytest.raises(RetailConfigError, match="Unknown RETAIL_PROVIDER"):
        build_provider(app.config)


class _Counting(RetailProvider):
    name = "counting"

    def __init__(self):
        self.calls = 0

    def search_products(self, query, lat, lng, radius_km=15, category=None):
        self.calls += 1
        return [_product(query, "10", "111")]

    def get_offers_by_barcode(self, barcode, lat=None, lng=None, radius_km=15):
        self.calls += 1
        return []

    def get_price_history(self, barcode, store_id):
        self.calls += 1
        return []


def test_results_are_cached_for_fifteen_minutes(app):
    assert app.config["RETAIL_CACHE_TTL"] == 15 * 60
    cache.init_app(app, config={"CACHE_TYPE": "SimpleCache"})
    inner = _Counting()
    provider = CachedProvider(inner, 900)
    first = provider.search_products("Milk", -29.8587, 31.0218)
    assert provider.search_products("  milk ", -29.8590, 31.0220) == first  # same query, ~same place
    assert inner.calls == 1
    provider.search_products("milk", -29.5, 31.0)  # elsewhere: new lookup
    provider.search_products("bread", -29.8587, 31.0218)
    assert inner.calls == 3
    provider.get_offers_by_barcode("111"), provider.get_offers_by_barcode("111")
    assert inner.calls == 4  # empty results are cached too


def test_failures_are_not_cached(app):
    cache.init_app(app, config={"CACHE_TYPE": "SimpleCache"})

    class Flaky(_Counting):
        def search_products(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RetailAPIError("boom")
            return [_product("ok", "1", "1")]

    provider = CachedProvider(Flaky(), 900)
    with pytest.raises(RetailAPIError):
        provider.search_products("milk", *DURBAN)
    assert provider.search_products("milk", *DURBAN)[0].name == "ok"


def test_cli_retail_search_with_the_mock(app):
    result = app.test_cli_runner().invoke(args=["retail-search", "Ariel"])
    assert (
        result.exit_code == 0 and "Ariel Auto Washing Powder 2kg" in result.output and "Provider: mock" in result.output
    )


def test_cli_reports_config_errors_plainly(app):
    app.config.update(RETAIL_PROVIDER="apify", APIFY_API_TOKEN=None)
    result = app.test_cli_runner().invoke(args=["retail-search", "milk"])
    assert result.exit_code != 0 and "APIFY_API_TOKEN" in result.output
