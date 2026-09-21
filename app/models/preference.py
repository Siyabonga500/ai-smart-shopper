"""Preference model - PDF page 3 (fields) and page 21 (code).

The "Manage Preferences" mock-up (PDF page 17) groups preferences as
Preferred Stores, Preferred Categories, Preferred Colours and Interests;
each is one ``PreferenceType`` with many ``PreferenceValue`` rows.
"""

from app.extensions import db
from app.utils.ids import PREFERENCE_PREFIX, generate_id


class Preference(db.Model):
    __tablename__ = "preferences"

    PreferenceId = db.Column(db.String(30), primary_key=True, default=lambda: generate_id(PREFERENCE_PREFIX))
    UserId = db.Column(db.String(30), db.ForeignKey("users.UserId", ondelete="CASCADE"), nullable=False)
    PreferenceType = db.Column(db.String(50), nullable=False)  # e.g., 'Dietary', 'Brand', 'Store'
    PreferenceValue = db.Column(db.String(255), nullable=False)  # e.g., 'Vegetarian', 'Checkers', 'Healthy'
