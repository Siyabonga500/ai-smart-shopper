"""The shopping route (Step 13): which store to visit first, and the road distance and time of the trip.

Order (:func:`order_stops`): the trip starts at the student's home and visits every store once. The first store is
the one with the fewest items on the list (a short first stop, then the bulk shop is left for last, when the
trolley matters least), and the rest are ordered so the total distance is as short as possible. With up to
``MAX_EXACT_STOPS`` stores every order is tried; with more, the nearest unvisited store is taken each time. The
order uses straight-line distances, so it needs no network and the page can show it at once.

Road figures (:func:`road_route`): the public OSRM server gives the road distance, the driving time and the line
to draw. OSRM's demo server is for light use only, so answers are cached and the call is made once per trip
shape. When OSRM cannot be reached the route is *estimated* from straight-line distances (x ``ROAD_FACTOR``, at
``CITY_SPEED_KMH``) and marked ``"source": "estimate"`` so the page says so.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass

import requests
from flask import current_app

from app.extensions import cache
from app.services.http import ExternalAPIError, request_with_retry
from app.utils.geo import distance_km

SERVICE = "osrm"
MAX_EXACT_STOPS = 7
ROAD_FACTOR = 1.3  # roads are longer than the crow flies
CITY_SPEED_KMH = 30.0  # average speed used for the estimate
FALLBACK_CACHE_SECONDS = 60  # after an OSRM failure, do not ask again for a minute


@dataclass(frozen=True)
class Stop:
    name: str
    lat: float
    lng: float
    item_count: int
    units: int = 0
    address: str | None = None

    def as_dict(self, order: int) -> dict:
        return {
            "order": order,
            "name": self.name,
            "address": self.address,
            "lat": self.lat,
            "lng": self.lng,
            "item_count": self.item_count,
        }


def stops_from_groups(groups) -> tuple[list[Stop], list[str]]:
    """``(stops, unlocated)``: the stores that have a map position, and the names of those that do not."""
    stops, unlocated = [], []
    for group in groups:
        if group.located:
            stops.append(Stop(group.store_name, group.lat, group.lng, group.item_count, group.units, group.address))
        else:
            unlocated.append(group.store_name)
    return stops, unlocated


def _path_length(home: dict, order, return_home: bool) -> float:
    points = [(home["lat"], home["lng"])] + [(s.lat, s.lng) for s in order]
    if return_home:
        points.append(points[0])
    return sum(distance_km(*a, *b) for a, b in zip(points, points[1:]))


def order_stops(home: dict, stops: list[Stop], return_home: bool = True) -> list[Stop]:
    """Fewest-items store first, then the shortest way round the others (see the module docstring)."""
    if len(stops) <= 1:
        return list(stops)
    fewest = min(s.item_count for s in stops)
    starts = [s for s in stops if s.item_count == fewest]

    if len(stops) <= MAX_EXACT_STOPS:
        best = None
        for first in starts:
            rest = [s for s in stops if s is not first]
            for permutation in itertools.permutations(rest):
                order = (first, *permutation)
                key = (round(_path_length(home, order, return_home), 6), tuple(s.name for s in order))
                if best is None or key < best[0]:
                    best = (key, order)
        return list(best[1])

    here = min(starts, key=lambda s: (distance_km(home["lat"], home["lng"], s.lat, s.lng), s.name))
    order, left = [here], [s for s in stops if s is not here]
    while left:
        nxt = min(left, key=lambda s: (distance_km(order[-1].lat, order[-1].lng, s.lat, s.lng), s.name))
        order.append(nxt)
        left.remove(nxt)
    return order


# ---------------------------------------------------------------------------------------------- road route
def _points(home: dict, order: list[Stop], return_home: bool) -> list[tuple[float, float]]:
    points = [(home["lat"], home["lng"])] + [(s.lat, s.lng) for s in order]
    if return_home:
        points.append(points[0])
    return points


def _estimate(points: list[tuple[float, float]]) -> dict:
    km = sum(distance_km(*a, *b) for a, b in zip(points, points[1:])) * ROAD_FACTOR
    return {
        "distance_km": round(km, 1),
        "duration_min": max(1, round(km / CITY_SPEED_KMH * 60)),
        "geometry": [[lat, lng] for lat, lng in points],
        "source": "estimate",
    }


def _osrm(points: list[tuple[float, float]]) -> dict:
    cfg = current_app.config
    coordinates = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
    url = f"{cfg['OSRM_BASE_URL'].rstrip('/')}/route/v1/driving/{coordinates}"
    response = request_with_retry(
        SERVICE,
        "GET",
        url,
        params={"overview": "full", "geometries": "geojson", "steps": "false"},
        retries=1,
        timeout=cfg["OSRM_TIMEOUT"],
        headers={"User-Agent": cfg.get("NOMINATIM_USER_AGENT") or "AI-Smart-Shopper"},
    )
    if response.status_code != 200:
        raise ExternalAPIError(
            f"OSRM answered HTTP {response.status_code}", service=SERVICE, status=response.status_code
        )
    data = response.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise ExternalAPIError(f"OSRM could not route this trip ({data.get('code')})", service=SERVICE)
    route = data["routes"][0]
    line = [[lat, lng] for lng, lat in route["geometry"]["coordinates"]]
    return {
        "distance_km": round(route["distance"] / 1000, 1),
        "duration_min": max(1, round(route["duration"] / 60)),
        "geometry": line,
        "source": "osrm",
    }


def road_route(home: dict, order: list[Stop], return_home: bool = True) -> dict:
    """``{"distance_km", "duration_min", "geometry": [[lat, lng], ...], "source": "osrm" | "estimate"}``. Never raises."""
    points = _points(home, order, return_home)
    if len(points) < 2:
        return {"distance_km": 0.0, "duration_min": 0, "geometry": [list(p) for p in points], "source": "estimate"}
    key = "route:" + hashlib.sha1("|".join(f"{lat:.5f},{lng:.5f}" for lat, lng in points).encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        result, ttl = _osrm(points), current_app.config["ROUTE_CACHE_TTL"]
    except (ExternalAPIError, requests.RequestException, ValueError, KeyError, TypeError):
        current_app.logger.warning("OSRM route failed; using an estimate", exc_info=True)
        result, ttl = _estimate(points), FALLBACK_CACHE_SECONDS
    cache.set(key, result, timeout=ttl)
    return result


def plan(home: dict, groups, return_home: bool | None = None) -> dict:
    """Order the stores of ``groups`` and add the road figures. Used by the API; the page orders with :func:`order_stops`."""
    if return_home is None:
        return_home = current_app.config["ROUTE_RETURN_HOME"]
    stops, unlocated = stops_from_groups(groups)
    ordered = order_stops(home, stops, return_home)
    payload = {
        "home": home,
        "return_home": return_home,
        "unlocated": unlocated,
        "stops": [s.as_dict(i) for i, s in enumerate(ordered, 1)],
    }
    if ordered:
        payload.update(road_route(home, ordered, return_home))
    else:
        payload.update({"distance_km": 0.0, "duration_min": 0, "geometry": [], "source": "none"})
    return payload
