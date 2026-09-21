import pytest
import responses

from app.extensions import geocode_limiter
from app.services import geocoding
from app.services.geocoding import GeocodingError, coordinates_for_address, geocode, reverse_geocode
from app.utils.geo import distance_km

SEARCH = "https://nominatim.openstreetmap.org/search"
REVERSE = "https://nominatim.openstreetmap.org/reverse"

BEREA = [
    {"display_name": "79, Mansfield Road, Berea, Durban, 4001, South Africa", "lat": "-29.8517", "lon": "31.0003"},
    {"display_name": "Mansfield Road, Glenwood, Durban", "lat": "-29.8600", "lon": "30.9900"},
]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr("app.services.http.time.sleep", lambda _s: None)


@responses.activate
def test_geocode_returns_places_and_sends_the_policy_headers(app):
    responses.get(SEARCH, json=BEREA)
    results = geocode("79 Mansfield Road Berea")
    assert [r.display_name for r in results][0].startswith("79, Mansfield Road")
    assert results[0].lat == pytest.approx(-29.8517) and results[0].lng == pytest.approx(31.0003)

    request = responses.calls[0].request
    assert "AISmartShopper" in request.headers["User-Agent"]  # identifies the app (Nominatim policy)
    assert "countrycodes=za" in request.url and "viewbox=" in request.url and "format=jsonv2" in request.url


@responses.activate
def test_short_or_blank_queries_never_reach_the_network(app):
    assert geocode("  ") == [] and geocode("ab") == []
    assert len(responses.calls) == 0


@responses.activate
def test_unusable_rows_are_skipped(app):
    responses.get(SEARCH, json=[{"display_name": "x", "lat": "abc", "lon": "1"}, {"lat": "1", "lon": "1"}, BEREA[0]])
    assert len(geocode("mansfield")) == 1


@responses.activate
def test_upstream_failures_become_geocoding_errors(app):
    responses.get(SEARCH, status=500)
    with pytest.raises(GeocodingError):
        geocode("mansfield road")
    responses.replace(responses.GET, SEARCH, status=403)
    with pytest.raises(GeocodingError):
        geocode("mansfield road again")
    responses.replace(responses.GET, SEARCH, body="<html>", status=200)
    with pytest.raises(GeocodingError):
        geocode("another road")


@responses.activate
def test_reverse_geocode_builds_a_short_address(app):
    responses.get(
        REVERSE,
        json={
            "display_name": "long, long name",
            "address": {
                "house_number": "79",
                "road": "Mansfield Road",
                "suburb": "Berea",
                "city": "Durban",
                "postcode": "4001",
                "country": "South Africa",
            },
        },
    )
    assert reverse_geocode(-29.8517, 31.0003) == "79 Mansfield Road, Berea, Durban, 4001"


@responses.activate
def test_reverse_geocode_with_nothing_there_is_none(app):
    responses.get(REVERSE, json={"error": "Unable to geocode"})
    assert reverse_geocode(-29.0, 31.0) is None


def test_reverse_geocode_rejects_impossible_coordinates(app):
    with pytest.raises(ValueError):
        reverse_geocode(123, 31)


@responses.activate
def test_results_are_cached(app, monkeypatch):
    from app.extensions import cache

    app.config["CACHE_TYPE"] = "SimpleCache"
    cache.init_app(app, config={"CACHE_TYPE": "SimpleCache"})
    responses.get(SEARCH, json=BEREA)
    geocode("79 Mansfield Road")
    geocode("79  mansfield road ")  # same search after normalising spaces and case
    assert len(responses.calls) == 1


def test_calls_are_spaced_at_least_the_configured_interval(app, monkeypatch):
    slept = []
    monkeypatch.setattr(geocoding.time, "sleep", slept.append)
    app.config["NOMINATIM_MIN_INTERVAL"] = 5
    geocoding._last_request_at = geocoding.time.monotonic()
    geocoding._throttle()
    assert slept and 0 < slept[0] <= 5


@responses.activate
def test_coordinates_for_address_never_raises(app):
    responses.get(SEARCH, status=500)
    assert coordinates_for_address("somewhere in Durban") is None
    responses.replace(responses.GET, SEARCH, json=BEREA)
    assert coordinates_for_address("79 Mansfield Road") == (pytest.approx(-29.8517), pytest.approx(31.0003))


# ------------------------------------------------------------------------------ endpoints
@responses.activate
def test_geocode_endpoint(client):
    responses.get(SEARCH, json=BEREA)
    body = client.get("/api/geocode?q=mansfield+road").get_json()
    assert body["results"][0] == {"display_name": BEREA[0]["display_name"], "lat": -29.8517, "lng": 31.0003}


@pytest.mark.parametrize("query", ["", "ab", "x" * 201])
def test_geocode_endpoint_validates_the_query(client, query):
    assert client.get("/api/geocode", query_string={"q": query}).status_code == 400


@responses.activate
def test_reverse_endpoint(client):
    responses.get(REVERSE, json={"address": {"road": "Mansfield Road", "suburb": "Berea"}, "display_name": "x"})
    assert client.get("/api/reverse-geocode?lat=-29.85&lng=31.0").get_json() == {"address": "Mansfield Road, Berea"}


@pytest.mark.parametrize("qs", ["", "lat=abc&lng=1", "lat=95&lng=1", "lat=1&lng=181", "lat=nan&lng=1"])
def test_reverse_endpoint_validates_input(client, qs):
    assert client.get(f"/api/reverse-geocode?{qs}").status_code == 400


@responses.activate
def test_endpoints_report_502_when_the_geocoder_is_down(client):
    responses.get(SEARCH, status=500)
    responses.get(REVERSE, status=500)
    assert client.get("/api/geocode?q=mansfield").status_code == 502
    assert client.get("/api/reverse-geocode?lat=-29.8&lng=31").status_code == 502


@responses.activate
def test_endpoints_are_rate_limited_per_client(app, client):
    app.config["RATELIMIT_ENABLED"] = True
    geocode_limiter.limit = 3
    geocode_limiter.clear()
    responses.get(SEARCH, json=BEREA)
    codes = [client.get("/api/geocode?q=mansfield+road").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert client.get("/api/geocode?q=mansfield+road").headers["Retry-After"]
    geocode_limiter.clear()


# ------------------------------------------------------------------------------ Haversine
def test_distance_km_known_distances():
    durban, umhlanga = (-29.8587, 31.0218), (-29.7267, 31.0850)
    assert distance_km(*durban, *durban) == 0
    assert distance_km(*durban, *umhlanga) == pytest.approx(16.0, abs=0.6)
    assert distance_km(-29.8587, 31.0218, -26.2041, 28.0473) == pytest.approx(500.0, abs=15)  # Durban -> Johannesburg
    assert distance_km(*durban, *umhlanga) == pytest.approx(distance_km(*umhlanga, *durban))
    assert distance_km(0, 0, 0, 180) == pytest.approx(20015, rel=1e-3)  # half the globe
