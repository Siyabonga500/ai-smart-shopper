"""Store model (Step 7): supermarket branches around Durban.

Not part of the PDF's six models. It is reference data (seeded with ``flask seed-stores``), shared by
every user, so it has no ``UserId`` and is never touched by user cascade deletes.

``LocationSource`` says how trustworthy the coordinates are:
  * ``official``    taken from the retailer's own store page,
  * ``geocoded``    looked up from the street address with OpenStreetMap Nominatim,
  * ``approximate`` a hand-placed anchor near the shopping centre - good enough to sort by distance
                    but can be a few hundred metres out. Run ``flask seed-stores --geocode`` to refine.
"""

from sqlalchemy import func

from app.extensions import db
from app.utils.ids import STORE_PREFIX, generate_id

LOCATION_SOURCES = ("official", "geocoded", "approximate")


class Store(db.Model):
    __tablename__ = "stores"

    StoreId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(STORE_PREFIX))
    Slug = db.Column(db.String(100), nullable=False, unique=True)  # stable key used by the seeder
    Name = db.Column(db.String(100), nullable=False)  # "Checkers Gateway"
    Brand = db.Column(db.String(50), nullable=False, index=True)  # "Checkers"
    Suburb = db.Column(db.String(100), nullable=True)
    Address = db.Column(db.Text, nullable=False)
    Latitude = db.Column(db.Float, nullable=False)
    Longitude = db.Column(db.Float, nullable=False)
    LocationSource = db.Column(db.String(20), nullable=False, default="approximate")
    Phone = db.Column(db.String(30), nullable=True)
    OpeningHours = db.Column(db.String(255), nullable=True)
    StoreLink = db.Column(db.String(255), nullable=True)  # unused: students are never sent to a retailer's website
    DateCreated = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def to_dict(self, distance_km: float | None = None) -> dict:
        data = {
            "id": self.StoreId,
            "name": self.Name,
            "brand": self.Brand,
            "suburb": self.Suburb,
            "address": self.Address,
            "lat": self.Latitude,
            "lng": self.Longitude,
            "location_source": self.LocationSource,
            "phone": self.Phone,
            "opening_hours": self.OpeningHours,
        }
        if distance_km is not None:
            data["distance_km"] = round(distance_km, 2)
        return data
