"""Geo helpers. Durban is the default map centre (config.DEFAULT_MAP_CENTER)."""

from math import asin, cos, radians, sin, sqrt

from flask import current_app

EARTH_RADIUS_KM = 6371.0088


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine (great-circle) distance between two points in kilometres."""
    lng1, lng2 = lon1, lon2
    d_lat = radians(lat2 - lat1)
    d_lng = radians(lng2 - lng1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


haversine_km = distance_km  # original name, kept so older imports keep working


def is_valid_coordinate(lat, lng) -> bool:
    try:
        return -90 <= float(lat) <= 90 and -180 <= float(lng) <= 180
    except (TypeError, ValueError):
        return False


def default_map_center() -> dict:
    """``{"lat": ..., "lng": ...}`` for Durban, from app config."""
    return dict(current_app.config["DEFAULT_MAP_CENTER"])


def map_center_for(lat=None, lng=None) -> dict:
    """The user's coordinates when valid, otherwise Durban."""
    if lat is not None and lng is not None and is_valid_coordinate(lat, lng):
        return {"lat": float(lat), "lng": float(lng)}
    return default_map_center()
