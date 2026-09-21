"""Address <-> coordinates through Nominatim (OpenStreetMap).

Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/) drives the design:
  * an identifying User-Agent (``NOMINATIM_USER_AGENT``; put a contact e-mail in it),
  * at most one request per second (a process-wide throttle below),
  * results are cached (``GEOCODE_CACHE_TTL``), and
  * it is not used for search-as-you-type: the browser only calls it on "Find on map", a map click
    or "use my location".
Searches are biased (not restricted) towards greater Durban and limited to South Africa.
The data is (c) OpenStreetMap contributors, ODbL: the map keeps its attribution.
"""

import hashlib
import threading
import time
from dataclasses import dataclass

from flask import current_app

from app.data.dut_residences import DUT_RESIDENCES
from app.extensions import cache
from app.services.http import ExternalAPIError, request_with_retry
from app.utils.geo import is_valid_coordinate

SERVICE = "nominatim"

_throttle_lock = threading.Lock()
_last_request_at = 0.0


class GeocodingError(RuntimeError):
    """The geocoder could not be reached or gave an unusable answer."""


@dataclass(frozen=True)
class GeocodeResult:
    display_name: str
    lat: float
    lng: float

    def as_dict(self) -> dict:
        return {"display_name": self.display_name, "lat": self.lat, "lng": self.lng}


# --- plumbing ----------------------------------------------------------------------------------
def _throttle() -> None:
    """Block until at least ``NOMINATIM_MIN_INTERVAL`` seconds have passed since the last call."""
    global _last_request_at
    interval = current_app.config["NOMINATIM_MIN_INTERVAL"]
    with _throttle_lock:
        wait = _last_request_at + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _get(path: str, params: dict):
    cfg = current_app.config
    _throttle()
    try:
        response = request_with_retry(
            SERVICE,
            "GET",
            f"{cfg['NOMINATIM_BASE_URL'].rstrip('/')}{path}",
            params=params,
            headers={"User-Agent": cfg["NOMINATIM_USER_AGENT"], "Accept-Language": "en"},
            timeout=cfg["NOMINATIM_TIMEOUT"],
            retries=1,  # be gentle with the public server
        )
    except ExternalAPIError as exc:
        raise GeocodingError(str(exc)) from exc
    if response.status_code != 200:
        raise GeocodingError(f"Nominatim answered HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise GeocodingError("Nominatim returned invalid JSON") from exc


def _cache_key(kind: str, value: str) -> str:
    return f"geocode:{kind}:{hashlib.sha1(value.encode('utf-8')).hexdigest()}"


def _short_address(payload: dict) -> str:
    """``79 Mansfield Road, Berea, Durban, 4001`` from a Nominatim reverse result."""
    details = payload.get("address") or {}
    street = " ".join(filter(None, [details.get("house_number"), details.get("road")]))
    area = details.get("suburb") or details.get("neighbourhood") or details.get("quarter")
    city = details.get("city") or details.get("town") or details.get("village") or details.get("municipality")
    parts = [street, area, city, details.get("postcode")]
    short = ", ".join(part for part in parts if part)
    return short or payload.get("display_name", "")


# --- public API ----------------------------------------------------------------------------------
def geocode(query: str, limit: int = 5) -> list[GeocodeResult]:
    """Places matching a typed address, best first. Empty list when nothing matches."""
    query = " ".join((query or "").split())
    if len(query) < 3:
        return []
    local = _local_residence_match(query)
    if local:
        return local[:limit]
    key = _cache_key("search", f"{limit}|{query.lower()}")
    cached = cache.get(key)
    if cached is not None:
        return [GeocodeResult(**item) for item in cached]

    cfg = current_app.config
    left, top, right, bottom = cfg["GEOCODE_VIEWBOX"]
    payload = _get(
        "/search",
        {
            "q": query,
            "format": "jsonv2",
            "limit": limit,
            "countrycodes": cfg["GEOCODE_COUNTRY_CODES"],
            "viewbox": f"{left},{top},{right},{bottom}",
            "bounded": 0,
            "dedupe": 1,
        },
    )
    if not isinstance(payload, list):
        raise GeocodingError("Unexpected response from Nominatim")

    results = []
    for item in payload:
        try:
            lat, lng = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if is_valid_coordinate(lat, lng) and item.get("display_name"):
            results.append(GeocodeResult(display_name=item["display_name"], lat=lat, lng=lng))
    cache.set(key, [r.as_dict() for r in results], timeout=cfg["GEOCODE_CACHE_TTL"])
    return results


def _local_residence_match(query: str) -> list[GeocodeResult]:
    """Resolve known DUT residences without a network call when the public geocoder is unavailable."""
    needle = " ".join(query.casefold().replace(",", " ").split())
    matches = []
    for residence in DUT_RESIDENCES:
        if residence.latitude is None or residence.longitude is None:
            continue
        haystacks = (residence.name, residence.full_address, residence.address)
        if any(needle == " ".join(value.casefold().replace(",", " ").split()) for value in haystacks):
            matches.append(GeocodeResult(residence.full_address, residence.latitude, residence.longitude))
    return matches


def reverse_geocode(lat: float, lng: float) -> str | None:
    """A short street address for a point, or ``None`` when there is nothing there."""
    if not is_valid_coordinate(lat, lng):
        raise ValueError("Latitude or longitude out of range")
    key = _cache_key("reverse", f"{round(float(lat), 5)},{round(float(lng), 5)}")
    cached = cache.get(key)
    if cached is not None:
        return cached or None

    payload = _get(
        "/reverse",
        {
            "lat": round(float(lat), 6),
            "lon": round(float(lng), 6),
            "format": "jsonv2",
            "zoom": 18,
            "addressdetails": 1,
        },
    )
    address = "" if not isinstance(payload, dict) or payload.get("error") else _short_address(payload)
    cache.set(key, address, timeout=current_app.config["GEOCODE_CACHE_TTL"])
    return address or None


def coordinates_for_address(address: str) -> tuple[float, float] | None:
    """Best-effort ``(lat, lng)`` for saving on a profile. Never raises: returns ``None`` on any failure."""
    try:
        results = geocode(address, limit=1)
    except GeocodingError:
        current_app.logger.warning("Could not geocode the address", exc_info=True)
        return None
    return (results[0].lat, results[0].lng) if results else None
