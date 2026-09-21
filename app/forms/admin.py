"""Forms for the admin portal (Step 16). Every POST is CSRF-protected by Flask-WTF."""

import re

from flask_wtf import FlaskForm
from wtforms import DecimalField, PasswordField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, Regexp, ValidationError

from app.forms.validators import cellphone_10_digits, name_field, normalise_text
from app.models.admin import MAPPED_CATEGORIES
from app.models.store import LOCATION_SOURCES
from app.services.admin_users import ROLES

# South Africa, generously: catches swapped or mistyped coordinates without refusing border towns or islands.
SA_LAT = (-35.0, -22.0)
SA_LNG = (16.0, 33.0)


def _blank(value):
    """Turn an empty string into ``None`` so optional columns stay NULL."""
    value = normalise_text(value)
    return value or None


class AdminUserForm(FlaskForm):
    FirstName = StringField(
        "First name",
        filters=[normalise_text],
        validators=[DataRequired("Enter the first name."), name_field("First name")],
    )
    LastName = StringField(
        "Last name",
        filters=[normalise_text],
        validators=[DataRequired("Enter the last name."), name_field("Last name")],
    )
    CellphoneNumber = StringField(
        "Cellphone number", validators=[DataRequired("Enter the cellphone number."), cellphone_10_digits]
    )
    ResidentialAddress = TextAreaField(
        "Residential address",
        filters=[_blank],
        validators=[Optional(), Length(max=300, message="Address is too long (300 characters max).")],
    )
    Role = SelectField("Role", choices=[(role, role) for role in ROLES], validate_choice=True)


class CategoryMappingForm(FlaskForm):
    RawCategory = StringField(
        "Category name from the API",
        filters=[normalise_text],
        validators=[
            DataRequired("Enter the category name the API uses."),
            Length(max=150, message="At most 150 characters."),
        ],
    )
    MappedCategory = SelectField("Maps to", choices=[(c, c) for c in MAPPED_CATEGORIES], validate_choice=True)


class ApiKeyForm(FlaskForm):
    ApiKey = PasswordField(
        "Key",
        validators=[
            DataRequired("Paste the key first."),
            Length(min=8, max=512, message="A key is between 8 and 512 characters."),
        ],
    )


_PHONE_RE = re.compile(r"^[0-9+()\-\s]{7,30}$")


class StoreForm(FlaskForm):
    Name = StringField(
        "Store name",
        filters=[normalise_text],
        validators=[
            DataRequired("Enter the store name."),
            Length(min=2, max=100, message="Between 2 and 100 characters."),
        ],
    )
    Brand = StringField(
        "Brand",
        filters=[normalise_text],
        validators=[
            DataRequired("Enter the brand, for example Checkers."),
            Length(max=50, message="At most 50 characters."),
        ],
    )
    Suburb = StringField(
        "Suburb", filters=[_blank], validators=[Optional(), Length(max=100, message="At most 100 characters.")]
    )
    Address = TextAreaField(
        "Address",
        filters=[normalise_text],
        validators=[DataRequired("Enter the street address."), Length(max=500, message="At most 500 characters.")],
    )
    Latitude = DecimalField(
        "Latitude", places=None, validators=[DataRequired("Enter the latitude, for example -29.8587.")]
    )
    Longitude = DecimalField(
        "Longitude", places=None, validators=[DataRequired("Enter the longitude, for example 31.0218.")]
    )
    LocationSource = SelectField(
        "How was the position found?", choices=[(s, s.capitalize()) for s in LOCATION_SOURCES], validate_choice=True
    )
    OpeningHours = StringField(
        "Opening hours", filters=[_blank], validators=[Optional(), Length(max=255, message="At most 255 characters.")]
    )
    Phone = StringField(
        "Phone",
        filters=[_blank],
        validators=[Optional(), Regexp(_PHONE_RE, message="Use digits, spaces, + ( ) and - only.")],
    )

    def validate_Latitude(self, field):
        self._in_range(field, SA_LAT, "latitude", "between -35 and -22 (South Africa)")

    def validate_Longitude(self, field):
        self._in_range(field, SA_LNG, "longitude", "between 16 and 33 (South Africa)")

    @staticmethod
    def _in_range(field, bounds, label, text):
        value = float(field.data)
        if not bounds[0] <= value <= bounds[1]:
            raise ValidationError(f"The {label} should be {text}. Did you swap latitude and longitude?")
