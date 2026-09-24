"""LoyaltyHub and Buyly connectors, tested against mocked HTTP (neither has been called live: they need your keys)."""

import pytest
import requests
import responses

from app.services import http, retail_api
from app.services.retail_api import (
    BuylyProvider,
    LoyaltyHubProvider,
    ParseBotProvider,
    RetailAPIError,
    RetailConfigError,
    build_provider,
    clean_image_url,
)

DURBAN = (-29.8587, 31.0218)
LH = "https://loyaltyhub.co.za/api/v1/prices"
BUYLY_SEARCH = "https://api.buyly.co.za/v1/products/search"
MILK = "6001030002075"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)


def _row(retailer="shoprite", barcode=MILK, name="Clover Full Cream Milk 2L", price=31.99, **extra):
    return {
        "retailer": retailer,
        "barcode": barcode,
        "name": name,
        "price": price,
        "in_stock": True,
        "image_url": "https://cdn.example/milk.jpg",
        **extra,
    }


def _envelope(rows, quota=100, used=1):
    return {
        "data": rows,
        "meta": {"count": len(rows), "total": len(rows), "has_more": False, "quota": quota, "used": used},
    }


# ------------------------------------------------------------------------------------------------ LoyaltyHub
@responses.activate
def test_loyaltyhub_search_returns_prices_barcodes_and_pictures(app):
    responses.get(LH, json=_envelope([_row("shoprite"), _row("pnp", price=33.49), _row("checkers", price=32.0)]))
    provider = LoyaltyHubProvider("lh_live_key", page_size=50)
    products = provider.search_products("milk", *DURBAN)

    assert {p.retailer for p in products} == {"Shoprite", "Pick n Pay", "Checkers"}
    assert all(p.barcode == MILK and p.image_url == "https://cdn.example/milk.jpg" for p in products)
    assert sorted(str(p.price) for p in products) == ["31.99", "32.00", "33.49"]
    assert all(p.category == "Grocery" and p.in_stock for p in products)
    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer lh_live_key"
    assert "search=milk" in request.url and "limit=50" in request.url
    assert provider.quota == {"quota": 100, "used": 1}


@responses.activate
def test_loyaltyhub_never_gives_students_a_retailer_website(app):
    responses.get(LH, json=_envelope([_row(url="https://www.shoprite.co.za/p/1", link="https://x.example")]))
    (product,) = LoyaltyHubProvider("k").search_products("milk", *DURBAN)
    assert "https://www.shoprite.co.za/p/1" not in repr(product) and "https://x.example" not in repr(product)


@responses.activate
def test_loyaltyhub_unknown_retailers_get_a_readable_name_and_bad_rows_are_skipped(app):
    responses.get(
        LH,
        json=_envelope(
            [
                _row("food-lovers-market"),
                _row("boxer", price=None),  # no price: unusable
                _row("makro", name=""),  # no name: unusable
                "not a row",
                _row("Woolworths", barcode="12345"),  # 5 digits is not a GTIN: kept, but ungrouped
            ]
        ),
    )
    products = LoyaltyHubProvider("k").search_products("milk", *DURBAN)
    assert sorted(p.retailer for p in products) == ["Food Lovers Market", "Woolworths"]
    assert {p.retailer: p.barcode for p in products}["Woolworths"] is None


@responses.activate
def test_loyaltyhub_upgrades_http_pictures_and_drops_unusable_ones(app):
    responses.get(
        LH,
        json=_envelope(
            [
                _row("shoprite", image_url="http://cdn.example/a.jpg"),
                _row("checkers", image_url="/relative/a.jpg"),
                _row("spar", image_url=None),
            ]
        ),
    )
    images = {p.retailer: p.image_url for p in LoyaltyHubProvider("k").search_products("milk", *DURBAN)}
    assert images == {"Shoprite": "https://cdn.example/a.jpg", "Checkers": None, "SPAR": None}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://a.example/x.jpg", "https://a.example/x.jpg"),
        ("http://a.example/x.jpg", "https://a.example/x.jpg"),
        ("//a.example/x.jpg", "https://a.example/x.jpg"),
        ({"url": "https://a.example/x.jpg"}, "https://a.example/x.jpg"),
        ("javascript:alert(1)", None),
        ("data:image/png;base64,AAAA", None),
        ("x.jpg", None),
        ("https://a.example/with space.jpg", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_clean_image_url(raw, expected):
    assert clean_image_url(raw) == expected


@responses.activate
def test_loyaltyhub_barcode_lookup_asks_for_the_barcode_and_keeps_exact_matches(app):
    responses.get(
        LH, json=_envelope([_row("pnp", price=33.49), _row("shoprite"), _row("spar", barcode="6009999999993")])
    )
    provider = LoyaltyHubProvider("k")
    offers = provider.get_offers_by_barcode(MILK, *DURBAN)
    assert [(o.retailer, str(o.price)) for o in offers] == [("Shoprite", "31.99"), ("Pick n Pay", "33.49")]
    assert f"barcode={MILK}" in responses.calls[0].request.url
    assert provider.get_product_by_barcode(MILK, *DURBAN).retailer == "Shoprite"


@responses.activate
def test_loyaltyhub_bad_barcodes_and_empty_searches_cost_no_calls(app):
    provider = LoyaltyHubProvider("k")
    assert provider.get_offers_by_barcode("12345") == [] and provider.search_products("  ", *DURBAN) == []
    assert len(responses.calls) == 0


@responses.activate
def test_loyaltyhub_browsing_a_category_without_text_sends_the_category(app):
    responses.get(LH, json=_envelope([_row()]))
    assert len(LoyaltyHubProvider("k").search_products("", *DURBAN, category="Toiletries")) == 1
    assert "category=Toiletries" in responses.calls[0].request.url and "search=" not in responses.calls[0].request.url


@responses.activate
def test_loyaltyhub_category_filter_uses_the_rows_own_category(app):
    responses.get(LH, json=_envelope([_row("shoprite", category="Dairy"), _row("checkers")]))
    found = LoyaltyHubProvider("k").search_products("milk", *DURBAN, category="Grocery")
    assert [p.retailer for p in found] == ["Checkers"]  # the row that says "Dairy" needs an admin category mapping


@responses.activate
def test_loyaltyhub_explains_bad_keys_quota_and_outages(app):
    provider = LoyaltyHubProvider("secret-key-123")
    for status, text in ((401, "rejected"), (403, "rejected"), (500, "failed"), (418, "HTTP 418")):
        responses.upsert(responses.GET, LH, status=status)
        with pytest.raises(RetailAPIError, match=text) as info:
            provider.search_products("milk", *DURBAN)
        assert "secret-key-123" not in str(info.value)


@responses.activate
def test_loyaltyhub_quota_exhaustion_is_reported_plainly_and_retried_only_once(app):
    responses.get(LH, status=429, headers={"Retry-After": "3600"})
    with pytest.raises(RetailAPIError, match="call limit") as info:
        LoyaltyHubProvider("k").search_products("milk", *DURBAN)
    assert info.value.status == 429 and len(responses.calls) == 2  # one try + one retry, not four


@responses.activate
def test_loyaltyhub_network_failures_surface_as_retail_errors_the_pages_handle(app):
    responses.get(LH, body=requests.ConnectionError("down"))
    with pytest.raises(RetailAPIError):
        LoyaltyHubProvider("k").search_products("milk", *DURBAN)


@responses.activate
def test_loyaltyhub_invalid_json_and_a_404_barcode(app):
    responses.get(LH, body="<html>oops</html>", content_type="text/html")
    with pytest.raises(RetailAPIError, match="invalid JSON"):
        LoyaltyHubProvider("k").search_products("milk", *DURBAN)
    responses.upsert(responses.GET, LH, status=404)
    assert LoyaltyHubProvider("k").get_offers_by_barcode(MILK, *DURBAN) == []


def test_loyaltyhub_configuration(app):
    with pytest.raises(RetailConfigError, match="LOYALTYHUB_API_KEY"):
        LoyaltyHubProvider(None)
    assert LoyaltyHubProvider("k", page_size=9999).page_size == 500  # the API's own ceiling
    app.config.update(LOYALTYHUB_API_KEY="k", RETAIL_PROVIDER="loyaltyhub")
    provider = build_provider(app.config)
    assert isinstance(provider, LoyaltyHubProvider) and provider.cache_ttl == 900
    assert provider.base_url == "https://loyaltyhub.co.za/api/v1"


def test_loyaltyhub_answers_refresh_every_fifteen_minutes(app):
    app.config.update(LOYALTYHUB_API_KEY="k", RETAIL_PROVIDER="loyaltyhub")
    retail_api.reset_retail_provider()
    provider = retail_api.get_retail_provider()
    assert provider.name == "loyaltyhub" and provider.ttl == 900
    app.config["RETAIL_PROVIDER"] = "mock"
    retail_api.reset_retail_provider()
    assert retail_api.get_retail_provider().ttl == app.config["RETAIL_CACHE_TTL"]


# ------------------------------------------------------------------------------------------------ Buyly
@responses.activate
def test_buyly_search_maps_a_plain_list(app):
    responses.get(
        BUYLY_SEARCH,
        json=[
            {
                "name": "Albany Brown Bread 700g",
                "ean": "6001069002206",
                "price": "R 17,99",
                "retailer": "Pick n Pay",
                "imageUrl": "https://img.example/bread.png",
            },
            {
                "title": "Albany Brown Bread 700g",
                "gtin": "6001069002206",
                "price": {"amount": 16.99},
                "store": {"name": "Shoprite"},
            },
        ],
    )
    products = BuylyProvider("buyly-key").search_products("bread", *DURBAN)
    assert {(p.retailer, str(p.price)) for p in products} == {("Pick n Pay", "17.99"), ("Shoprite", "16.99")}
    assert {p.image_url for p in products} == {"https://img.example/bread.png", None}
    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer buyly-key" and "q=bread" in request.url


@responses.activate
def test_buyly_search_expands_a_product_with_a_list_of_retailer_prices(app):
    responses.get(
        BUYLY_SEARCH,
        json={
            "data": {
                "products": [
                    {
                        "name": "Coca-Cola 2L",
                        "barcode": "5449000000996",
                        "image": {"url": "https://img.example/coke.png"},
                        "prices": [{"retailer": "Checkers", "price": 24.99}, {"retailer": "Spar", "price": 26.5}],
                    }
                ]
            }
        },
    )
    products = BuylyProvider("k").search_products("coke", *DURBAN)
    assert {(p.retailer, str(p.price)) for p in products} == {("Checkers", "24.99"), ("SPAR", "26.50")}
    assert all(p.barcode == "5449000000996" and p.image_url == "https://img.example/coke.png" for p in products)
    assert not any(hasattr(p, "store_link") for p in products)


@responses.activate
def test_buyly_barcode_lookup_accepts_a_bare_product_and_fills_in_the_barcode(app):
    responses.get(
        f"https://api.buyly.co.za/v1/products/{MILK}",
        json={"product": {"name": "Milk 2L", "price": 30, "retailer": "Woolworths"}},
    )
    offers = BuylyProvider("k").get_offers_by_barcode(MILK, *DURBAN)
    assert [(o.retailer, o.barcode, str(o.price)) for o in offers] == [("Woolworths", MILK, "30.00")]


@responses.activate
def test_buyly_errors_are_explained_and_a_missing_barcode_is_just_empty(app):
    provider = BuylyProvider("k")
    responses.get(BUYLY_SEARCH, status=401)
    with pytest.raises(RetailAPIError, match="rejected"):
        provider.search_products("milk", *DURBAN)
    responses.upsert(responses.GET, BUYLY_SEARCH, status=500)
    with pytest.raises(RetailAPIError):
        provider.search_products("milk", *DURBAN)
    responses.get(f"https://api.buyly.co.za/v1/products/{MILK}", status=404)
    assert provider.get_offers_by_barcode(MILK) == []
    assert provider.search_products("", *DURBAN) == []


def test_buyly_configuration(app):
    with pytest.raises(RetailConfigError, match="BUYLY_API_KEY"):
        BuylyProvider("")
    app.config.update(BUYLY_API_KEY="k", RETAIL_PROVIDER="buyly")
    assert isinstance(build_provider(app.config), BuylyProvider)


@responses.activate
def test_parsebot_network_failures_are_retail_errors_too(app):
    """Regression: a dropped connection used to escape as a bare ExternalAPIError and turn into a 500 page."""
    responses.get(
        "https://api.parse.bot/scraper/3c08f360-415f-4984-bcd1-846bc0d7dafc/search_products",
        body=requests.ConnectionError("down"),
    )
    with pytest.raises(RetailAPIError):
        ParseBotProvider("k").search_products("milk", *DURBAN)


# ------------------------------------------------------------------------------------------------ no retailer links
def _walk(value, path=""):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _walk(inner, f"{path}.{key}")
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _walk(inner, f"{path}[{index}]")
    else:
        yield path, value


def _assert_no_retailer_links(payload):
    for path, value in _walk(payload):
        assert "link" not in path.casefold() and not path.endswith(".url"), path
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            assert path.endswith(("image_url", "image")), f"{path} = {value}"  # pictures are the only web addresses


def test_no_student_response_contains_a_retailer_website(client, make_user, login):
    from decimal import Decimal

    from app.services import budgets

    user = make_user(Latitude=-29.8587, Longitude=31.0218)
    budgets.create_budget(user.UserId, "Sep Budget", [("Grocery", Decimal("800"))])
    login(user)
    barcode = "2000000000275"
    _assert_no_retailer_links(client.get("/api/search?q=milk").get_json())
    detail = client.get(f"/api/product?barcode={barcode}").get_json()
    _assert_no_retailer_links(detail)
    added = client.post("/api/list/items", json={"barcode": barcode, "store_name": detail["product"]["store_name"]})
    assert added.status_code == 201
    _assert_no_retailer_links(added.get_json())
    _assert_no_retailer_links(client.get("/api/list").get_json())
    _assert_no_retailer_links(client.get("/api/stores/near?lat=-29.8587&lng=31.0218").get_json())


def test_the_search_page_script_has_no_retailer_page_button(app):
    from pathlib import Path

    script = (Path(app.root_path) / "static" / "js" / "search.js").read_text(encoding="utf-8")
    assert "Retailer page" not in script and "store_link" not in script


def test_the_mock_catalogue_shows_a_picture_for_every_category(app):
    from app.services.retail_api import MockProvider

    images = {
        p.category: p.image_url
        for q in ("milk", "shampoo", "shirt", "mouse")
        for p in MockProvider().search_products(q, *DURBAN)
    }
    assert set(images) >= {"Grocery", "Toiletries"} and len(set(images.values())) == len(images)
    from pathlib import Path

    for url in images.values():
        assert (Path(app.root_path) / url.removeprefix("/")).is_file(), url


# ------------------------------------------------------------------------------------------------ LoyaltyHub paging
def _page(rows, has_more):
    return {"data": rows, "meta": {"count": len(rows), "has_more": has_more, "quota": 100, "used": 1}}


def _rows(start, count):
    return [_row(barcode=f"60010300{n:05d}", name=f"Milk {n}") for n in range(start, start + count)]


@responses.activate
def test_loyaltyhub_follows_more_pages_when_the_api_says_there_are_more(app):
    responses.get(LH, json=_page(_rows(0, 10), True))
    responses.get(LH, json=_page(_rows(10, 10), True))
    responses.get(LH, json=_page(_rows(20, 4), False))
    products = LoyaltyHubProvider("k").search_products("milk", *DURBAN)
    assert len(products) == 24 and len(responses.calls) == 3
    second = responses.calls[1].request.url
    assert "page=2" in second and "offset=10" in second and "search=milk" in second


@responses.activate
def test_loyaltyhub_paging_stops_at_max_pages_and_on_repeated_pages(app):
    for _ in range(5):
        responses.get(LH, json=_page(_rows(0, 10), True))  # an API that ignores page/offset: same rows again
    assert len(LoyaltyHubProvider("k").search_products("milk", *DURBAN)) == 10
    assert len(responses.calls) == 2  # the repeat added nothing, so no third call

    responses.calls.reset()
    responses.replace(responses.GET, LH, json=_page(_rows(0, 10), True))
    provider = LoyaltyHubProvider("k", max_pages=1)
    assert len(provider.search_products("bread", *DURBAN)) == 10 and len(responses.calls) == 1


@responses.activate
def test_loyaltyhub_total_and_last_page_also_mean_more(app):
    responses.get(LH, json={"data": _rows(0, 10), "meta": {"total": 15}})
    responses.get(LH, json={"data": _rows(10, 5), "meta": {"total": 15}})
    assert len(LoyaltyHubProvider("k").search_products("milk", *DURBAN)) == 15
