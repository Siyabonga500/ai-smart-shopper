"""Store queries: stores within a radius of a point, nearest first."""

from math import cos, radians

from sqlalchemy import select

from app.extensions import db
from app.models import Store
from app.utils.geo import distance_km

KM_PER_DEGREE_LAT = 111.32


def stores_near(lat: float, lng: float, radius_km: float, brand: str | None = None, limit: int | None = None):
    """``[(Store, distance_km), ...]`` within ``radius_km`` of the point, nearest first.

    A cheap bounding-box filter in SQL narrows the rows, then the exact Haversine distance decides.
    """
    d_lat = radius_km / KM_PER_DEGREE_LAT
    d_lng = radius_km / (KM_PER_DEGREE_LAT * max(cos(radians(lat)), 0.01))
    query = select(Store).where(
        Store.Latitude.between(lat - d_lat, lat + d_lat),
        Store.Longitude.between(lng - d_lng, lng + d_lng),
    )
    if brand:
        query = query.where(Store.Brand == brand)

    found = []
    for store in db.session.scalars(query):
        distance = distance_km(lat, lng, store.Latitude, store.Longitude)
        if distance <= radius_km:
            found.append((store, distance))
    found.sort(key=lambda pair: (pair[1], pair[0].Name))
    return found[:limit] if limit else found


def store_brands() -> list[str]:
    return list(db.session.scalars(select(Store.Brand).distinct().order_by(Store.Brand)))
