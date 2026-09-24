"""Profile forms (Step 5)."""

from flask import current_app
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import BooleanField, HiddenField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError

from app.data.dut_residences import DUT_RESIDENCES, OTHER_ACCOMMODATION, get_residence, residence_choices
from app.forms.validators import cellphone_10_digits, name_field, normalise_text, parse_coordinates, password_policy


class ProfileForm(FlaskForm):
    FirstName = StringField(
        "First name",
        filters=[normalise_text],
        validators=[DataRequired("Enter your first name."), name_field("First name")],
    )
    LastName = StringField(
        "Last name",
        filters=[normalise_text],
        validators=[DataRequired("Enter your last name."), name_field("Last name")],
    )
    CellphoneNumber = StringField(
        "Cellphone number", validators=[DataRequired("Enter your cellphone number."), cellphone_10_digits]
    )
    Residence = SelectField("DUT residence", validate_choice=True, default="")  # optional: "" = not chosen
    ResidentialAddress = StringField(
        "Residential address",
        filters=[normalise_text],
        validators=[Length(max=300, message="Address is too long (300 characters max).")],
    )
    Latitude = HiddenField()
    Longitude = HiddenField()
    ProfilePic = FileField(
        "Profile picture", validators=[FileAllowed(["jpg", "jpeg", "png", "webp"], "Choose a JPG, PNG or WEBP image.")]
    )
    RemovePhoto = BooleanField("Remove my current photo")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.Residence.choices = [("", "Select your residence…")] + residence_choices()

    def select_residence_for(self, address: str | None) -> None:
        """Pre-select the DUT residence whose address the student saved (``other`` for any other address)."""
        wanted = (address or "").strip().casefold()
        match = next((r.key for r in DUT_RESIDENCES if r.full_address.casefold() == wanted), None)
        self.Residence.data = match or (OTHER_ACCOMMODATION if wanted else "")

    def validate_ResidentialAddress(self, field):
        residence = get_residence(self.Residence.data)
        if not field.data and residence:
            field.data = residence.full_address  # residence picked but address left empty
        if not field.data:
            raise ValidationError("Enter your residential address, or pick it on the map.")

    @property
    def coordinates(self):
        return parse_coordinates(self.Latitude.data, self.Longitude.data)


class ChangePasswordForm(FlaskForm):
    CurrentPassword = PasswordField("Current password")
    NewPassword = PasswordField("New password", validators=[DataRequired("Enter a new password."), password_policy])
    ConfirmPassword = PasswordField(
        "Confirm new password",
        validators=[
            DataRequired("Confirm your new password."),
            EqualTo("NewPassword", message="Passwords do not match."),
        ],
    )

    def __init__(self, *args, require_current: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_current = require_current


class PreferenceForm(FlaskForm):
    PreferenceType = SelectField("Type", validate_choice=True)
    PreferenceValue = StringField(
        "Value",
        filters=[normalise_text],
        validators=[DataRequired("Enter a value."), Length(max=100, message="Keep it under 100 characters.")],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.PreferenceType.choices = [(t, t) for t in current_app.config["PREFERENCE_TYPES"]]
