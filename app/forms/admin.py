"""Forms for the admin portal (Step 16). Every POST is CSRF-protected by Flask-WTF."""

import re

from flask_wtf import FlaskForm
from flask_wtf.file import MultipleFileField
from wtforms import BooleanField, DecimalField, IntegerField, PasswordField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, Regexp, ValidationError

from app.forms.validators import cellphone_10_digits, email_address, name_field, normalise_text, password_policy
from app.models.admin import MAPPED_CATEGORIES
from app.models.catalogue import PRODUCT_CATEGORIES, STOCK_STATUSES
from app.models.store import LOCATION_SOURCES, STORE_TYPES
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
    StoreType = SelectField(
        "What does it sell?",
        default="grocery",
        choices=[
            (t, {"grocery": "Groceries and toiletries", "clothing": "Clothing", "both": "Both"}[t]) for t in STORE_TYPES
        ],
        validate_choice=True,
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


class AdminNewUserForm(FlaskForm):
    """Admin > Users > Add user: an account that can sign in straight away with the password typed here."""

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
    Email = StringField(
        "Email",
        filters=[lambda v: (v or "").strip().lower() or None],
        validators=[DataRequired("Enter the email address."), Length(max=100), email_address],
    )
    CellphoneNumber = StringField("Cellphone number", validators=[Optional(), cellphone_10_digits])
    Password = PasswordField("Password", validators=[DataRequired("Choose a password."), password_policy])
    Role = SelectField("Role", choices=[(role, role) for role in ROLES], validate_choice=True)


_BARCODE_RE = re.compile(r"^[0-9]{8,14}$")
_SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,49}$")


def _lines(value):
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


class ProductForm(FlaskForm):
    """Admin > Products: Category -> Store -> the fields that category needs.

    Grocery and Toiletries: name, barcode, price, image, stock status.
    Clothing: name, brand, SKU, photos, size, colour, price, stock.
    """

    Category = SelectField("Category", choices=[(c, c) for c in PRODUCT_CATEGORIES], validate_choice=True)
    StoreId = SelectField("Store", choices=[], validate_choice=False)
    Name = StringField(
        "Product name",
        filters=[normalise_text],
        validators=[
            DataRequired("Enter the product name."),
            Length(min=2, max=150, message="Between 2 and 150 characters."),
        ],
    )
    # No Optional() on the fields below: it would skip the validate_* methods that make them required per category.
    Brand = StringField("Brand", filters=[_blank], validators=[Length(max=80, message="At most 80 characters.")])
    Barcode = StringField("Barcode", filters=[_blank])
    Sku = StringField("SKU", filters=[_blank])
    Price = DecimalField(
        "Price (R)",
        places=2,
        validators=[
            DataRequired("Enter the price, for example 24.99."),
            NumberRange(min=0.01, max=100000, message="The price must be between R0.01 and R100 000."),
        ],
    )
    SalePrice = DecimalField(
        "Sale price (R)",
        places=2,
        validators=[Optional(), NumberRange(min=0.01, max=100000, message="The sale price must be more than R0.")],
    )
    ImageUrl = StringField(
        "Image URL", filters=[_blank], validators=[Optional(), Length(max=1000, message="At most 1000 characters.")]
    )
    ImageFile = MultipleFileField("Or upload a picture")
    PhotoUrls = TextAreaField("Picture URLs (one per line)", filters=[_blank], validators=[Optional()])
    Photos = MultipleFileField("Upload pictures")
    RemovePictures = BooleanField("Remove the current uploaded pictures")
    Size = StringField("Size", filters=[_blank], validators=[Length(max=30, message="At most 30 characters.")])
    Colour = StringField("Colour", filters=[_blank], validators=[Length(max=40, message="At most 40 characters.")])
    StockStatus = SelectField("Stock status", choices=list(STOCK_STATUSES), validate_choice=True)
    StockQuantity = IntegerField(
        "Stock quantity", validators=[Optional(), NumberRange(min=0, max=1000000, message="Enter 0 or more.")]
    )

    def __init__(self, *args, stores=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.stores = {s["id"]: s for s in stores}
        self.StoreId.choices = [("", "Choose a store…")] + [(s["id"], f"{s['name']} — {s['address']}") for s in stores]

    @property
    def is_clothing(self) -> bool:
        return self.Category.data == "Clothing"

    @property
    def photo_urls(self) -> list[str]:
        return _lines(self.PhotoUrls.data)

    @staticmethod
    def _http_url(value: str) -> bool:
        return value.lower().startswith(("https://", "http://")) and " " not in value

    def validate_StoreId(self, field):
        store = self.stores.get(field.data)
        if store is None:
            raise ValidationError("Choose the store that sells this product.")
        if self.is_clothing and store["type"] not in ("clothing", "both"):
            raise ValidationError(f"{store['name']} does not sell clothing. Choose a clothing store.")
        if not self.is_clothing and store["type"] not in ("grocery", "both"):
            raise ValidationError(f"{store['name']} is a clothing store. Choose a supermarket.")

    def validate_Barcode(self, field):
        if self.is_clothing:
            if field.data and not _BARCODE_RE.match(field.data):
                raise ValidationError("A barcode is 8 to 14 digits.")
            return
        if not field.data:
            raise ValidationError("Enter the barcode (the numbers under the stripes).")
        if not _BARCODE_RE.match(field.data):
            raise ValidationError("A barcode is 8 to 14 digits, for example 6001087359992.")

    def validate_Sku(self, field):
        if not self.is_clothing:
            return
        if not field.data:
            raise ValidationError("Enter the SKU (the retailer's product code).")
        if not _SKU_RE.match(field.data):
            raise ValidationError("Use letters, digits and . _ / - only (2 to 50 characters).")

    def validate_Brand(self, field):
        if self.is_clothing and not field.data:
            raise ValidationError("Enter the brand.")

    def validate_Size(self, field):
        if self.is_clothing and not field.data:
            raise ValidationError("Enter the size, for example M or 32.")

    def validate_Colour(self, field):
        if self.is_clothing and not field.data:
            raise ValidationError("Enter the colour.")

    def validate_ImageUrl(self, field):
        if field.data and not self._http_url(field.data):
            raise ValidationError("The image URL must start with https:// or http://.")

    def validate_PhotoUrls(self, field):
        for line in self.photo_urls:
            if not self._http_url(line) or len(line) > 1000:
                raise ValidationError(f"“{line[:60]}” is not a web address (it must start with https://).")

    def validate_SalePrice(self, field):
        if field.data is not None and self.Price.data is not None and field.data >= self.Price.data:
            raise ValidationError("The sale price must be lower than the normal price. Leave it empty if not on sale.")

    def validate(self, extra_validators=None):
        valid = super().validate(extra_validators)
        # Checked here: Optional() on the field stops its own validators when it is empty.
        if self.is_clothing and self.StockQuantity.data is None and not self.StockQuantity.errors:
            self.StockQuantity.errors = [*self.StockQuantity.errors, "Enter how many are in stock (0 if none)."]
            valid = False
        return valid
