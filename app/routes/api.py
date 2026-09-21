"""JSON endpoints used by the front-end: geocoding (public) and stores (signed in)."""

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from app.extensions import geocode_limiter
from app.services.geocoding import GeocodingError, geocode, reverse_geocode
from app.services.stores import store_brands, stores_near
from app.utils.geo import is_valid_coordinate, map_center_for

bp = Blueprint("api", __name__, url_prefix="/api")


def _rate_limited():
    """A 429 response when this client used up its geocoding allowance, else ``None``."""
    if not current_app.config["RATELIMIT_ENABLED"]:
        return None
    if geocode_limiter.hit(f"geocode:{request.remote_addr or 'unknown'}"):
        return None
    response = jsonify(error="Too many address lookups. Please wait a minute and try again.")
    response.status_code = 429
    response.headers["Retry-After"] = str(int(current_app.config["GEOCODE_REQUEST_WINDOW"]))
    return response


def _unavailable():
    return jsonify(error="Address lookup is unavailable right now."), 502


@bp.get("/geocode")
def geocode_address():
    """``GET /api/geocode?q=79 Mansfield Road, Berea`` -> ``{"results": [{display_name, lat, lng}]}``.

    Public on purpose: the registration page uses it before the student has an account.
    """
    query = " ".join(request.args.get("q", "").split())
    if len(query) < 3 or len(query) > 200:
        return jsonify(error="Enter between 3 and 200 characters to search for."), 400
    if (limited := _rate_limited()) is not None:
        return limited
    try:
        results = geocode(query)
    except GeocodingError:
        current_app.logger.warning("Geocoding failed", exc_info=True)
        return _unavailable()
    return jsonify(results=[r.as_dict() for r in results])


@bp.get("/reverse-geocode")
def reverse_geocode_point():
    """``GET /api/reverse-geocode?lat=-29.85&lng=31.02`` -> ``{"address": "..." | null}``."""
    try:
        lat, lng = float(request.args["lat"]), float(request.args["lng"])
    except (KeyError, ValueError):
        return jsonify(error="lat and lng are required numbers."), 400
    if not is_valid_coordinate(lat, lng) or lat != lat or lng != lng:
        return jsonify(error="lat or lng is out of range."), 400
    if (limited := _rate_limited()) is not None:
        return limited
    try:
        address = reverse_geocode(lat, lng)
    except GeocodingError:
        current_app.logger.warning("Reverse geocoding failed", exc_info=True)
        return _unavailable()
    return jsonify(address=address)


@bp.get("/stores/near")
@login_required
def stores_near_point():
    """``GET /api/stores/near?lat=&lng=&radius=`` -> stores within ``radius`` km, nearest first.

    ``lat``/``lng`` default to the student's saved address, then to Durban. ``radius`` (km) defaults to
    ``STORE_DEFAULT_RADIUS_KM`` and is capped at ``STORE_MAX_RADIUS_KM``. ``brand`` optionally filters.
    Each store carries ``distance_km``.
    """
    cfg = current_app.config
    raw_lat, raw_lng = request.args.get("lat"), request.args.get("lng")
    if (raw_lat is None) != (raw_lng is None):
        return jsonify(error="Send both lat and lng, or neither."), 400
    if raw_lat is None:
        center = map_center_for(current_user.Latitude, current_user.Longitude)
    else:
        try:
            lat, lng = float(raw_lat), float(raw_lng)
        except ValueError:
            return jsonify(error="lat and lng must be numbers."), 400
        if lat != lat or lng != lng or not is_valid_coordinate(lat, lng):
            return jsonify(error="lat or lng is out of range."), 400
        center = {"lat": lat, "lng": lng}

    try:
        radius = float(request.args.get("radius", cfg["STORE_DEFAULT_RADIUS_KM"]))
    except ValueError:
        return jsonify(error="radius must be a number of kilometres."), 400
    if not 0 < radius < float("inf"):
        return jsonify(error="radius must be greater than zero."), 400
    radius = min(radius, cfg["STORE_MAX_RADIUS_KM"])

    brand = (request.args.get("brand") or "").strip() or None
    found = stores_near(center["lat"], center["lng"], radius, brand=brand)
    return jsonify(
        center=center,
        radius_km=radius,
        count=len(found),
        brands=store_brands(),
        stores=[store.to_dict(distance) for store, distance in found],
    )
