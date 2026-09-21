"""Step 15: the recommendation engine, cheaper alternatives, potential savings, and their two JSON endpoints."""

import math
from datetime import timedelta
from decimal import Decimal

import pytest

from app.data.mock_products import CATALOGUE
from app.extensions import db, search_limiter
from app.models import ListItem, Preference
from app.services import budgets, recommender, shopping
from app.services.retail_api import PricePoint, RetailAPIError, RetailProvider, get_retail_provider
from app.utils.dates import utcnow

D = Decimal
HOME = {"lat": -29.8587, "lng": 31.0218}
MILK = "2000000000275"
CHOC_MILK = "2000000000305"
BY_BARCODE = {item.barcode: item for item in CATALOGUE}
GROCERY = [i.barcode for i in CATALOGUE if i.category == "Grocery"]
TOILETRIES = [i.barcode for i in CATALOGUE if i.category == "Toiletries"]


# ------------------------------------------------------------------------------------------------ fixtures / helpers
@pytest.fixture
def user(make_user):
    return make_user(Latitude=HOME["lat"], Longitude=HOME["lng"])


@pytest.fixture
def signed_in(client, user, login):
    login(user)
    return client


@pytest.fixture
def provider(app):
    return get_retail_provider()


def buy(user, barcode, days_ago=5, qty=1, when=None):
    """One finished purchase of ``barcode`` (a closed list with one bought row)."""
    item = BY_BARCODE[barcode]
    budget = budgets.create_budget(user.UserId, f"Trip {barcode} {days_ago}", [("Grocery", D("5000"))])
    shopping_list = budgets.get_active_list(budget)
    shopping_list.IsActive = False
    budget.IsActive = False
    row = ListItem(
        UserId=user.UserId,
        ShoppingListId=shopping_list.ShoppingListId,
        ItemName=item.name,
        BarCode=barcode,
        Category=item.category,
        UnitCost=D(item.price),
        ItemQuantity=qty,
        StoreName="Checkers",
        IsPurchased=True,
        PurchasedDate=when or utcnow() - timedelta(days=days_ago),
    )
    db.session.add(row)
    db.session.commit()
    return row


def offers(provider, barcode=MILK):
    return sorted(
        [o for o in provider.get_offers_by_barcode(barcode, HOME["lat"], HOME["lng"], 15) if o.in_stock],
        key=lambda o: (o.price, o.store_name),
    )


def on_list(user, offer, qty=1):
    result = shopping.add_product(user, offer)
    if qty > 1:
        shopping.set_quantity(user, result.item.ListItemId, qty)
    return result.item


class FakeProvider(RetailProvider):
    """A provider whose offers and price histories are whatever the test says."""

    name = "fake"

    def __init__(self, by_barcode=None, browse=None, history=None):
        self.by_barcode, self.browse, self.history = by_barcode or {}, browse or {}, history or {}
        self.history_calls = 0

    def search_products(self, query, lat, lng, radius_km=15, category=None):
        return list(self.browse.get(category, []))

    def get_offers_by_barcode(self, barcode, lat=None, lng=None, radius_km=15):
        return list(self.by_barcode.get(barcode, []))

    def get_stores_near(self, lat, lng, radius_km=15):
        return []

    def get_price_history(self, barcode, store_id):
        self.history_calls += 1
        result = self.history.get((barcode, store_id), [])
        if isinstance(result, Exception):
            raise result
        return result


def offer(
    barcode="1",
    name="Thing",
    price="10",
    store="A",
    category="Grocery",
    brand="Acme",
    in_stock=True,
    distance=1.0,
    store_id=None,
):
    from app.services.retail_api import Product

    return Product(
        name=name,
        barcode=barcode,
        price=D(price),
        image_url=None,
        store_name=store,
        store_address=None,
        store_lat=None,
        store_lng=None,
        category=category,
        in_stock=in_stock,
        brand=brand,
        retailer=store,
        store_id=store_id or f"{store}-id",
        distance_km=distance,
    )


def points(*prices):
    return [PricePoint(utcnow().date() - timedelta(weeks=len(prices) - i), D(str(p))) for i, p in enumerate(prices)]


# ================================================================================================ recently purchased
def test_frequency_first_then_most_recent(app, user):
    for days in (3, 10, 20):
        buy(user, MILK, days)
    buy(user, CHOC_MILK, 2)
    buy(user, GROCERY[0], 40)
    result = recommender.recently_purchased(user.UserId)
    assert [p.barcode for p in result] == [MILK, CHOC_MILK, GROCERY[0]]
    assert result[0].times == 3 and result[1].times == 1


def test_tags_follow_the_last_purchase_date(app, user):
    buy(user, MILK, 3)
    buy(user, CHOC_MILK, 60)
    tags = {p.barcode: p.tag for p in recommender.recently_purchased(user.UserId)}
    assert tags == {MILK: "Recently purchased", CHOC_MILK: "Lastly purchased"}


def test_the_last_purchase_date_is_the_newest_one(app, user):
    buy(user, MILK, 80)
    buy(user, MILK, 2)
    (only,) = recommender.recently_purchased(user.UserId)
    assert only.tag == "Recently purchased" and only.times == 2


def test_only_the_last_90_days_count(app, user):
    buy(user, MILK, 89)
    buy(user, CHOC_MILK, 91)
    assert [p.barcode for p in recommender.recently_purchased(user.UserId)] == [MILK]


def test_limit_and_quantity(app, user):
    buy(user, MILK, 3, qty=4)
    buy(user, MILK, 4, qty=2)
    for barcode in GROCERY[:5]:
        buy(user, barcode, 6)
    assert len(recommender.recently_purchased(user.UserId, limit=3)) == 3
    assert recommender.recently_purchased(user.UserId, limit=0) == []
    assert next(p for p in recommender.recently_purchased(user.UserId) if p.barcode == MILK).quantity == 6


def test_products_without_a_barcode_are_told_apart_by_name(app, user):
    for _ in range(2):
        row = buy(user, MILK, 5)
        row.BarCode = None
        row.ItemName = "Loose Apples"
        db.session.commit()
    (only,) = recommender.recently_purchased(user.UserId)
    assert only.key == "loose apples" and only.times == 2


def test_only_own_purchased_items_count(app, user, make_user, provider):
    buy(make_user(), MILK, 2)
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("900"))])
    on_list(user, offers(provider)[0])  # on the list, not bought
    assert recommender.recently_purchased(user.UserId) == []


# ================================================================================================ cosine + collaborative
def test_cosine_basics():
    assert recommender.cosine({"a": 1, "b": 1}, {"a": 1, "b": 1}) == pytest.approx(1.0)
    assert recommender.cosine({"a": 1}, {"b": 1}) == 0.0
    assert recommender.cosine({"a": 1, "b": 1}, {"a": 1, "c": 1}) == pytest.approx(0.5)
    assert recommender.cosine({"a": 2, "b": 0}, {"a": 5}) == pytest.approx(1.0)  # scale does not matter
    assert recommender.cosine({}, {"a": 1}) == 0.0 and recommender.cosine({"a": 1}, {}) == 0.0
    assert recommender.cosine({"a": 3, "b": 4}, {"a": 4, "b": 3}) == pytest.approx(24 / 25)


def test_similar_students_lend_their_other_purchases_weighted_by_similarity(app, user, make_user):
    a, b, c, d, x = GROCERY[:5]
    for barcode in (a, b):
        buy(user, barcode)
    twin, half, stranger = make_user(), make_user(), make_user()
    for barcode in (a, b, c):
        buy(twin, barcode)
    for barcode in (a, d):
        buy(half, barcode)
    buy(stranger, x)
    scores = recommender.collaborative_scores(user.UserId)
    assert set(scores) == {c, d}  # nothing from the stranger, nothing already owned
    sim_twin, sim_half = 2 / math.sqrt(6), 1 / 2
    assert scores[c] == pytest.approx(sim_twin / (sim_twin + sim_half))
    assert scores[d] == pytest.approx(sim_half / (sim_twin + sim_half))
    assert scores[c] > scores[d]


def test_a_student_with_no_history_or_no_neighbours_gets_nothing(app, user, make_user):
    assert recommender.collaborative_scores(user.UserId) == {}
    buy(user, MILK)
    buy(make_user(), CHOC_MILK)
    assert recommender.collaborative_scores(user.UserId) == {}


def test_deactivated_students_and_old_purchases_are_left_out(app, user, make_user):
    a, b, c = GROCERY[:3]
    buy(user, a)
    gone, stale = make_user(), make_user()
    for barcode in (a, b):
        buy(gone, barcode)
    for barcode in (a, c):
        buy(stale, barcode, days_ago=120)
    gone.IsActive = False
    db.session.commit()
    assert recommender.collaborative_scores(user.UserId) == {}


def test_only_barcodes_and_scores_cross_between_students(app, user, make_user):
    other = make_user(first="Nomsa", last="Zulu")
    for barcode in GROCERY[:2]:
        buy(user, barcode)
        buy(other, barcode)
    buy(other, GROCERY[2])
    scores = recommender.collaborative_scores(user.UserId)
    assert list(scores) == [GROCERY[2]] and all(isinstance(v, float) for v in scores.values())
    assert "Nomsa" not in repr(scores) and other.UserId not in repr(scores)


# ================================================================================================ hybrid recommender
def test_unknown_user_or_zero_limit(app, user, provider):
    assert recommender.recommend_for_user("USR-nope", provider=provider) == []
    assert recommender.recommend_for_user(user.UserId, limit=0, provider=provider) == []


def test_content_based_follows_category_preferences(app, user, provider):
    db.session.add(Preference(UserId=user.UserId, PreferenceType="Category", PreferenceValue="Toiletries"))
    db.session.commit()
    recs = recommender.recommend_for_user(user.UserId, limit=6, provider=provider)
    assert recs and all(r.offer.category == "Toiletries" for r in recs)
    assert {r.tag for r in recs} <= {"Matches your preferences", "On promotion", "Cheaper alternative"}
    assert all(r.reasons for r in recs)


def test_content_based_follows_what_was_bought(app, user, provider):
    for barcode in TOILETRIES[:3]:
        buy(user, barcode, 10)
    recs = recommender.recommend_for_user(user.UserId, limit=6, provider=provider)
    assert recs and recs[0].offer.category == "Toiletries"
    assert any(r.tag == "Because you buy Toiletries" for r in recs)
    assert not {r.offer.barcode for r in recs} & set(TOILETRIES[:3])  # bought lately: not "recommended"


def test_a_preferred_brand_ranks_first_within_its_category(app, user, provider):
    target = next(i for i in CATALOGUE if i.category == "Toiletries")
    db.session.add_all(
        [
            Preference(UserId=user.UserId, PreferenceType="Category", PreferenceValue="Toiletries"),
            Preference(UserId=user.UserId, PreferenceType="Brand", PreferenceValue=target.brand),
        ]
    )
    db.session.commit()
    recs = recommender.recommend_for_user(user.UserId, limit=8, provider=provider)
    assert recs[0].offer.brand == target.brand and "A brand you prefer" in recs[0].reasons


def test_items_on_the_active_list_are_not_recommended(app, user, provider):
    db.session.add(Preference(UserId=user.UserId, PreferenceType="Category", PreferenceValue="Grocery"))
    db.session.commit()
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("900"))])
    first = recommender.recommend_for_user(user.UserId, limit=12, provider=provider)[0]
    on_list(user, first.offer)
    again = {r.offer.barcode for r in recommender.recommend_for_user(user.UserId, limit=12, provider=provider)}
    assert first.offer.barcode not in again


def test_collaborative_candidates_come_with_their_reason(app, user, make_user, provider):
    a, b, c = GROCERY[:3]
    for barcode in (a, b):
        buy(user, barcode)
        buy(make_user(), barcode)
    twin = make_user()
    for barcode in (a, b, c):
        buy(twin, barcode)
    recs = recommender.recommend_for_user(user.UserId, limit=20, provider=provider)
    found = next(r for r in recs if r.offer.barcode == c)
    assert found.collaborative > 0 and "Bought by students with a similar shopping history" in found.reasons


def test_scores_are_sorted_and_within_the_limit(app, user, provider):
    for barcode in GROCERY[:4]:
        buy(user, barcode, 10)
    recs = recommender.recommend_for_user(user.UserId, limit=5, provider=provider)
    assert len(recs) == 5
    assert [r.score for r in recs] == sorted((r.score for r in recs), reverse=True)
    assert len({r.offer.barcode for r in recs}) == 5  # one card per product


def test_a_new_student_gets_deals_near_you_only(app, user, provider):
    recs = recommender.recommend_for_user(user.UserId, limit=6, provider=provider)
    assert recs and all(r.tag in ("Deal near you", "On promotion") for r in recs)
    assert all(r.price > 0 and r.reasons for r in recs)


def test_price_alone_does_not_recommend_once_the_student_has_tastes(app, user):
    """With a preferred category, a big price gap in some *other* category adds nothing."""
    grocery = [offer("g1", "Grocery One", "10", "A"), offer("g1", "Grocery One", "20", "B")]
    other = [
        offer("t1", "Soap", "5", "A", category="Toiletries"),
        offer("t1", "Soap", "50", "B", category="Toiletries"),
    ]
    fake = FakeProvider(browse={"Grocery": grocery, "Toiletries": other})
    db.session.add(Preference(UserId=user.UserId, PreferenceType="Category", PreferenceValue="Grocery"))
    db.session.commit()
    recs = recommender.recommend_for_user(user.UserId, provider=fake)
    assert [r.offer.barcode for r in recs] == ["g1"]


# ---- price-aware --------------------------------------------------------------------------------------
def test_promotion_is_a_price_well_under_the_stores_usual_price(app):
    o = offer(price="7")
    fake = FakeProvider(history={("1", "A-id"): points(10, 10, 10, 10, 10, 7)})
    assert recommender.promotion_discount(fake, o) == pytest.approx(0.3)
    fake.history[("1", "A-id")] = points(10, 10, 10, 10, 10, 9.5)
    assert recommender.promotion_discount(fake, offer(price="9.5")) == 0.0  # 5% is not a promotion
    fake.history[("1", "A-id")] = points(10, 10, 10)
    assert recommender.promotion_discount(fake, offer(price="5")) == 0.0  # too little history to say
    fake.history[("1", "A-id")] = points(10, 10, 10, 10, 12)
    assert recommender.promotion_discount(fake, offer(price="12")) == 0.0  # dearer than usual
    fake.history[("1", "A-id")] = RetailAPIError("down")
    assert recommender.promotion_discount(fake, offer(price="1")) == 0.0
    assert recommender.promotion_discount(fake, offer(barcode=None)) == 0.0  # no barcode: no history to ask for


def test_a_promotion_boosts_and_is_tagged(app, user):
    plain, promo = offer("p1", "Plain Rice", "10", "A"), offer("p2", "Promo Rice", "10", "A")
    fake = FakeProvider(browse={"Grocery": [plain, promo]}, history={("p2", "A-id"): points(15, 15, 15, 15, 15, 10)})
    db.session.add(Preference(UserId=user.UserId, PreferenceType="Category", PreferenceValue="Grocery"))
    db.session.commit()
    recs = recommender.recommend_for_user(user.UserId, provider=fake)
    assert [r.offer.barcode for r in recs][0] == "p2"
    assert recs[0].tag == "On promotion" and recs[0].reasons[0].startswith("On promotion: 33% below")
    assert recs[0].score > recs[1].score


def test_a_wide_price_gap_between_stores_adds_a_saving_reason(app, user):
    fake = FakeProvider(
        browse={"Grocery": [offer("g1", "Grocery One", "10", "A"), offer("g1", "Grocery One", "14", "B")]}
    )
    db.session.add(Preference(UserId=user.UserId, PreferenceType="Category", PreferenceValue="Grocery"))
    db.session.commit()
    (rec,) = recommender.recommend_for_user(user.UserId, provider=fake)
    assert rec.offer.store_name == "A" and rec.saving == D("4.00")
    assert any("Save R4 at A compared with B" == r for r in rec.reasons)


def test_the_cheaper_alternative_of_a_list_item_is_recommended(app, user, provider):
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("900"))])
    dearest = offers(provider)[-1]
    item = on_list(user, dearest)
    shopping.refresh_alternatives([item], provider, user)
    recs = recommender.recommend_for_user(user.UserId, provider=provider)
    alt = next(r for r in recs if r.tag == "Cheaper alternative")
    assert alt.offer.barcode == MILK and alt.offer.price < dearest.price
    assert alt.reasons[0].startswith(f"Cheaper than {item.ItemName} on your list: save R")
    assert alt.price == 1.0 and alt.saving > 0


def test_a_provider_outage_returns_what_it_has(app, user):
    class Down(FakeProvider):
        def search_products(self, *args, **kwargs):
            raise RetailAPIError("down")

    assert recommender.recommend_for_user(user.UserId, provider=Down()) == []


# ================================================================================================ the carousel + endpoint
def test_carousel_starts_with_up_to_four_favourites_then_recommendations(app, user, provider):
    for barcode in GROCERY[:6]:
        buy(user, barcode, 8)
    cards = recommender.carousel(user.UserId, limit=12, provider=provider)
    tags = [c["tag"] for c in cards]
    assert tags[:4] == ["Recently purchased"] * 4 and len(cards) == 12
    assert len({c["key"] for c in cards}) == len(cards)
    assert all(c["reasons"] for c in cards) and all(c["tag"] for c in cards)


def test_carousel_cards_show_what_is_already_on_the_list(app, user, provider):
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("900"))])
    buy_row = buy(user, MILK, 4)
    budgets.create_budget(user.UserId, "Sep2", [("Grocery", D("900"))])
    on_list(user, offers(provider)[0])
    cards = recommender.carousel(user.UserId, limit=4, provider=provider)
    milk = next(c for c in cards if c["barcode"] == MILK)
    assert milk["in_list_qty"] == 1 and buy_row.BarCode == MILK


def test_recommendations_endpoint(signed_in, user, provider):
    for barcode in GROCERY[:2]:
        buy(user, barcode, 6)
    body = signed_in.get("/api/recommendations").get_json()
    assert body["results"] and body["results"][0]["tag"] == "Recently purchased"
    assert len(signed_in.get("/api/recommendations?limit=3").get_json()["results"]) == 3
    assert signed_in.get("/api/recommendations?limit=abc").status_code == 400
    assert len(signed_in.get("/api/recommendations?limit=999").get_json()["results"]) <= 24


def test_recommendations_never_show_another_students_purchases(signed_in, user, make_user):
    other = make_user()
    for barcode in TOILETRIES[:3]:
        buy(other, barcode, 3)
    recs = signed_in.get("/api/recommendations").get_json()["results"]
    assert not [c for c in recs if c["tag"] in ("Recently purchased", "Lastly purchased")]


@pytest.mark.parametrize("url", ["/api/recommendations", "/api/savings"])
def test_endpoints_need_sign_in(client, url):
    assert client.get(url, headers={"Accept": "application/json"}).status_code in (302, 401)


@pytest.mark.parametrize("url", ["/api/recommendations", "/api/savings"])
def test_endpoints_are_rate_limited(app, signed_in, url):
    app.config["RATELIMIT_ENABLED"] = True
    search_limiter.limit = 2
    search_limiter.clear()
    try:
        assert [signed_in.get(url).status_code for _ in range(3)] == [200, 200, 429]
    finally:
        search_limiter.clear()


# ================================================================================================ cheaper alternatives
def test_cheaper_alternatives_are_the_cheapest_three_elsewhere_sorted(app, user, provider):
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("900"))])
    dearest = offers(provider)[-1]
    item = on_list(user, dearest)
    found = recommender.cheaper_alternatives(item, provider=provider)
    prices = [o.price for o in found]
    assert 1 <= len(found) <= 3 and prices == sorted(prices)
    assert all(
        o.price < dearest.price and o.store_name != dearest.store_name and o.in_stock and o.barcode == MILK
        for o in found
    )
    every = [o for o in offers(provider) if o.store_name != dearest.store_name and o.price < dearest.price]
    assert prices == sorted(o.price for o in every)[:3]


def test_nothing_cheaper_or_no_barcode_means_no_alternatives(app, user, provider):
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("900"))])
    item = on_list(user, offers(provider)[0])  # already the cheapest
    assert recommender.cheaper_alternatives(item, provider=provider) == []
    item.BarCode = None
    assert recommender.cheaper_alternatives(item, provider=provider) == []
    assert recommender.cheaper_alternatives(item, limit=0, provider=provider) == []


def test_cheaper_alternatives_skip_out_of_stock_the_same_store_and_ties(app, user):
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("900"))])
    here = offer("z", "Zed", "10", "Here")
    fake = FakeProvider(
        by_barcode={
            "z": [
                here,
                offer("z", "Zed", "6", "Gone", in_stock=False),
                offer("z", "Zed", "10", "Same"),
                offer("z", "Zed", "5", "Same-Store"),
                offer("z", "Zed", "7", "Far", distance=9.0),
                offer("z", "Zed", "7", "Near", distance=1.0),
                offer("z", "Zed", "8", "Third"),
                offer("z", "Zed", "9", "Fourth"),
            ]
        }
    )
    item = on_list(user, here)
    item.StoreName = "Here"
    names = [o.store_name for o in recommender.cheaper_alternatives(item, provider=fake)]
    assert names == ["Same-Store", "Near", "Far"] and "Gone" not in names


# ================================================================================================ potential savings
def test_potential_savings_sums_price_now_minus_cheapest_times_quantity(app, user, provider):
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("900"))])
    cheapest, dearest = offers(provider)[0], offers(provider)[-1]
    item = on_list(user, dearest, qty=3)
    assert recommender.potential_savings(user.UserId) == D("0.00")  # nothing checked yet
    shopping.refresh_alternatives([item], provider, user)
    assert recommender.potential_savings(user.UserId) == (dearest.price - cheapest.price) * 3


def test_savings_report_and_endpoint(signed_in, user, provider):
    empty = signed_in.get("/api/savings").get_json()
    assert empty == {"total": "0.00", "formatted": "R0", "count": 0, "items": [], "item_count": 0, "has_list": False}
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("900"))])
    cheapest, dearest = offers(provider)[0], offers(provider)[-1]
    on_list(user, dearest, qty=2)
    on_list(user, offers(provider, CHOC_MILK)[0])  # already the cheapest: no saving row
    body = signed_in.get("/api/savings").get_json()  # the endpoint re-checks alternatives itself
    saving = (dearest.price - cheapest.price) * 2
    assert D(body["total"]) == saving and body["formatted"] == f"R{saving:.2f}".replace(".00", "")
    assert body["count"] == 1 and body["item_count"] == 2 and body["has_list"] is True
    (row,) = body["items"]
    assert row["quantity"] == 2 and D(row["current_price"]) == dearest.price and D(row["saving"]) == saving
    assert row["cheapest"]["same_product"] is True and D(row["cheapest"]["price"]) == cheapest.price


def test_the_dashboard_and_the_api_agree(signed_in, user, provider):
    from app.services import dashboard

    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("900"))])
    on_list(user, offers(provider)[-1], qty=2)
    api_total = D(signed_in.get("/api/savings").get_json()["total"])
    assert dashboard.build(user).savings == api_total > 0


def test_savings_only_look_at_the_students_own_list(signed_in, user, make_user, provider):
    other = make_user(Latitude=HOME["lat"], Longitude=HOME["lng"])
    budgets.create_budget(other.UserId, "Theirs", [("Grocery", D("900"))])
    on_list(other, offers(provider)[-1])
    assert signed_in.get("/api/savings").get_json()["has_list"] is False
