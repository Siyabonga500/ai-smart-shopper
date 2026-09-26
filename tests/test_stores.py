import json

import pytest
import responses
from sqlalchemy import select

from app.cli import geocode_approximate_stores, upsert_stores
from app.data.durban_stores import ALL_STORES, DURBAN_STORES, REQUESTED_BRANCHES, unconfirmed_requests
from app.extensions import db
from app.models import Store
from app.services.stores import stores_near
from app.utils.geo import distance_km

DURBAN = (-29.8587, 31.0218)
SEARCH = "https://nominatim.openstreetmap.org/search"


@pytest.fixture
def seeded(app):
    upsert_stores(DURBAN_STORES)


@pytest.fixture
def signed_in(client, make_user, login):
    user = make_user()
    login(user)
    return user


def _near(client, **params):
    return client.get("/api/stores/near", query_string=params)


# ------------------------------------------------------------------------------ the data
def test_seed_data_is_sane():
    slugs = [seed.slug for seed in DURBAN_STORES]
    assert len(slugs) == len(set(slugs))
    for seed in DURBAN_STORES:
        # everything must be inside greater Durban (roughly 60 km around the centre)
        assert distance_km(*DURBAN, seed.lat, seed.lng) < 60, seed.name
        assert seed.source in ("official", "geocoded", "approximate")
        assert seed.name.startswith(seed.brand) or seed.name.startswith("SUPER" + seed.brand)
        assert seed.address and seed.link.startswith("https://")
    assert {seed.brand for seed in DURBAN_STORES} == set(REQUESTED_BRANCHES)


def test_every_requested_area_is_either_seeded_or_reported_as_unconfirmed():
    requested = {(b, a) for b, areas in REQUESTED_BRANCHES.items() for a in areas}
    covered = {(s.brand, a) for s in DURBAN_STORES for a in s.covers}
    assert covered <= requested  # nothing is claimed for an area nobody asked for
    assert covered | set(unconfirmed_requests()) == requested
    assert not covered & set(unconfirmed_requests())


def test_only_the_official_position_is_marked_official():
    official = [s.name for s in DURBAN_STORES if s.source == "official"]
    assert official == ["SUPERSPAR Glenwood"]


# ------------------------------------------------------------------------------ seeding
def test_seeding_is_idempotent(app):
    assert upsert_stores(DURBAN_STORES) == (len(DURBAN_STORES), 0)
    assert upsert_stores(DURBAN_STORES) == (0, len(DURBAN_STORES))
    assert len(db.session.scalars(select(Store)).all()) == len(DURBAN_STORES)
    assert all(store.StoreId.startswith("STR-") for store in db.session.scalars(select(Store)))


def test_reseeding_does_not_downgrade_a_better_position(app):
    upsert_stores(DURBAN_STORES)
    store = db.session.scalar(select(Store).where(Store.Slug == "checkers-gateway"))
    store.Latitude, store.Longitude, store.LocationSource = -29.7301, 31.0711, "geocoded"
    db.session.commit()
    upsert_stores(DURBAN_STORES)  # the seed only has an approximate anchor
    store = db.session.scalar(select(Store).where(Store.Slug == "checkers-gateway"))
    assert (store.Latitude, store.LocationSource) == (-29.7301, "geocoded")


def test_cli_seed_stores_and_dry_run(app):
    runner = app.test_cli_runner()
    dry = runner.invoke(args=["seed-stores", "--dry-run"])
    assert dry.exit_code == 0 and "would be seeded" in dry.output
    assert db.session.scalars(select(Store)).all() == []

    result = runner.invoke(args=["seed-stores"])
    assert result.exit_code == 0
    assert f"{len(ALL_STORES)} added" in result.output  # supermarkets and clothing shops
    assert "Not seeded" in result.output and "SPAR Musgrave" in result.output  # honest about the gaps
    assert len(db.session.scalars(select(Store)).all()) == len(ALL_STORES)
    assert db.session.scalar(select(Store).where(Store.Slug == "mrprice-gateway")).StoreType == "clothing"


def test_cli_can_load_extra_branches_from_json(app, tmp_path):
    extra = tmp_path / "extra.json"
    extra.write_text(
        json.dumps(
            [
                {
                    "slug": "spar-musgrave",
                    "name": "SPAR Musgrave",
                    "brand": "SPAR",
                    "suburb": "Musgrave",
                    "address": "Musgrave Centre, 115 Musgrave Road",
                    "lat": -29.8500,
                    "lng": 31.0000,
                }
            ]
        )
    )
    result = app.test_cli_runner().invoke(args=["seed-stores", "--file", str(extra)])
    assert result.exit_code == 0
    assert db.session.scalar(select(Store).where(Store.Slug == "spar-musgrave")).Brand == "SPAR"

    extra.write_text(json.dumps([{"name": "no slug"}]))
    assert app.test_cli_runner().invoke(args=["seed-stores", "--file", str(extra)]).exit_code != 0


@responses.activate
def test_geocode_option_refines_positions_but_rejects_far_away_matches(app, seeded):
    app.config["NOMINATIM_MIN_INTERVAL"] = 0

    def handler(request):
        query = request.params["q"]
        if "Gateway Theatre" in query:
            return (200, {}, json.dumps([{"display_name": "x", "lat": "-29.7300", "lon": "31.0710"}]))
        if "Bluff" in query or "Tara Road" in query:
            return (
                200,
                {},
                json.dumps([{"display_name": "wrong city", "lat": "-26.2", "lon": "28.0"}]),
            )  # Johannesburg
        return (200, {}, "[]")

    responses.add_callback(responses.GET, SEARCH, callback=handler)
    improved = geocode_approximate_stores(echo=lambda *_: None)
    assert improved == 1
    gateway = db.session.scalar(select(Store).where(Store.Slug == "checkers-gateway"))
    assert (gateway.LocationSource, gateway.Latitude) == ("geocoded", -29.73)
    bluff = db.session.scalar(select(Store).where(Store.Slug == "checkers-bluff-towers"))
    assert bluff.LocationSource == "approximate"  # the Johannesburg hit was refused
    assert db.session.scalar(select(Store).where(Store.Slug == "superspar-glenwood")).LocationSource == "official"


# ------------------------------------------------------------------------------ the query
def test_stores_near_sorts_by_distance_and_respects_the_radius(seeded):
    umhlanga = (-29.7268, 31.0700)
    found = stores_near(*umhlanga, radius_km=3)
    names = [store.Name for store, _ in found]
    assert "Checkers Gateway" in names and "SUPERSPAR Glenwood" not in names
    distances = [d for _, d in found]
    assert distances == sorted(distances) and all(d <= 3 for d in distances)
    assert found[0][1] < 0.5


def test_stores_near_can_filter_by_brand(seeded):
    found = stores_near(*DURBAN, radius_km=50, brand="Woolworths")
    assert {store.Brand for store, _ in found} == {"Woolworths"}
    assert len(found) == len([s for s in DURBAN_STORES if s.brand == "Woolworths"])


def test_stores_near_nothing_in_range(seeded):
    assert stores_near(-33.9, 18.4, radius_km=50) == []  # Cape Town


# ------------------------------------------------------------------------------ the endpoint
def test_endpoint_requires_sign_in(client):
    response = _near(client)
    assert response.status_code == 401 and response.get_json()["error"]


def test_endpoint_returns_json_sorted_by_distance_with_distance_km(client, signed_in, seeded):
    body = _near(client, lat=-29.7268, lng=31.0700, radius=5).get_json()
    assert body["center"] == {"lat": -29.7268, "lng": 31.07} and body["radius_km"] == 5
    stores = body["stores"]
    assert body["count"] == len(stores) > 3
    assert [s["distance_km"] for s in stores] == sorted(s["distance_km"] for s in stores)
    first = stores[0]
    assert set(first) >= {
        "id",
        "name",
        "brand",
        "address",
        "lat",
        "lng",
        "phone",
        "opening_hours",
        "distance_km",
        "location_source",
    }
    assert isinstance(first["distance_km"], float) and first["distance_km"] < 1
    assert "Checkers" in body["brands"]


def test_endpoint_defaults_to_the_users_address_then_to_durban(app, client, make_user, login, seeded):
    user = make_user(Latitude=-29.7268, Longitude=31.0700)
    login(user)
    assert _near(client, radius=1).get_json()["stores"][0]["name"].endswith("Gateway")  # Umhlanga home
    user.Latitude = user.Longitude = None
    db.session.commit()
    body = _near(client).get_json()
    assert body["center"] == app.config["DEFAULT_MAP_CENTER"] and body["radius_km"] == 10


def test_endpoint_caps_the_radius(client, signed_in, seeded):
    assert _near(client, lat=-29.86, lng=31.02, radius=9999).get_json()["radius_km"] == 60


@pytest.mark.parametrize(
    "params",
    [
        {"lat": "abc", "lng": "31"},
        {"lat": "-29"},
        {"lng": "31"},
        {"lat": "95", "lng": "31"},
        {"lat": "-29", "lng": "31", "radius": "x"},
        {"lat": "-29", "lng": "31", "radius": "0"},
        {"lat": "-29", "lng": "31", "radius": "-3"},
        {"lat": "nan", "lng": "31"},
        {"lat": "-29", "lng": "31", "radius": "inf"},
    ],
)
def test_endpoint_rejects_bad_parameters(client, signed_in, params):
    assert _near(client, **params).status_code == 400


def test_endpoint_brand_filter(client, signed_in, seeded):
    stores = _near(client, lat=-29.86, lng=31.02, radius=50, brand="SPAR").get_json()["stores"]
    assert {s["name"] for s in stores} == {seed.name for seed in DURBAN_STORES if seed.brand == "SPAR"}
    assert all(s["brand"] == "SPAR" for s in stores)


# ------------------------------------------------------------------------------ the search page
def test_search_page_offers_the_nearby_stores_as_a_filter(client, signed_in):
    html = client.get("/search").get_data(as_text=True)
    assert 'id="store-filter"' in html and "/api/stores/near" in html and 'id="radius"' in html
    assert 'data-radius-max="60"' in html and 'min="0"' in html  # the Distance filter goes from 0 to 60 km


def test_the_stores_the_search_page_lists_are_centred_on_the_users_address(client, make_user, login, seeded):
    login(make_user(Latitude=-29.7268, Longitude=31.07))  # Umhlanga: not the Durban default
    umhlanga = _near(client, radius=3).get_json()
    assert umhlanga["center"] == {"lat": -29.7268, "lng": 31.07}


def test_durban_cbd_branches_of_every_requested_chain():
    from app.data.durban_stores import ALL_STORES

    cbd = {seed.brand for seed in ALL_STORES if seed.suburb == "Durban Central"}
    assert {"Mr Price", "Jet", "SPAR", "Shoprite", "Boxer", "Markham", "Relay Jeans", "M&L"} <= cbd
    for seed in ALL_STORES:
        if seed.suburb == "Durban Central":
            assert distance_km(*DURBAN, seed.lat, seed.lng) < 3, seed.name  # all within the CBD
    kinds = {seed.brand: seed.kind for seed in ALL_STORES}
    assert kinds["Boxer"] == "grocery" and kinds["Markham"] == kinds["M&L"] == kinds["Relay Jeans"] == "clothing"
