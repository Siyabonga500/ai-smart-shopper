"""Registration and login forms (Step 3)."""

from flask import current_app
from flask_wtf import FlaskForm
from sqlalchemy import func, select
from wtforms import BooleanField, HiddenField, PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError

from app.data.dut_residences import OTHER_ACCOMMODATION, get_residence, residence_choices
from app.extensions import db
from app.forms.validators import (
    cellphone_10_digits,
    email_address,
    name_field,
    normalise_text,
    parse_coordinates,
    password_policy,
)
from app.models import User
from app.utils.sa_id import SAIdError, clean_sa_id, parse_sa_id


def _blank_first(choices):
    return [("", "Select…")] + [(value, value) for value in choices]


class RegistrationForm(FlaskForm):
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
    Email = StringField(
        "Email address",
        filters=[lambda v: v.strip().lower() if isinstance(v, str) else v],
        validators=[
            DataRequired("Enter your email address."),
            Length(max=100, message="Email address is too long."),
            email_address,
        ],
    )
    CellphoneNumber = StringField(
        "Cellphone number", validators=[DataRequired("Enter your cellphone number."), cellphone_10_digits]
    )
    SAIdNumber = StringField("South African ID number", validators=[DataRequired("Enter your 13-digit ID number.")])
    Gender = SelectField("Gender", validate_choice=True)
    Race = SelectField("Race", validate_choice=True, validators=[DataRequired("Select an option.")])
    Residence = SelectField("DUT residence", validate_choice=True)
    ResidentialAddress = StringField(
        "Residential address",
        filters=[normalise_text],
        validators=[Length(max=300, message="Address is too long (300 characters max).")],
    )
    Latitude = HiddenField()
    Longitude = HiddenField()
    Password = PasswordField("Password", validators=[DataRequired("Create a password."), password_policy])
    ConfirmPassword = PasswordField(
        "Confirm password",
        validators=[DataRequired("Confirm your password."), EqualTo("Password", message="Passwords do not match.")],
    )
    AcceptTerms = BooleanField(
        "I accept the Terms and Privacy Policy",
        validators=[DataRequired("You must accept the Terms to create an account.")],
    )

    def __init__(self, *args, microsoft: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        cfg = current_app.config
        self.Gender.choices = _blank_first(cfg["GENDER_CHOICES"])
        self.Race.choices = _blank_first(cfg["RACE_CHOICES"])
        self.Residence.choices = [("", "Select your residence…")] + residence_choices()
        self.microsoft = microsoft
        if microsoft:
            # Signed in with Microsoft: no password is created for this account.
            del self.Password
            del self.ConfirmPassword

    # --- field level -------------------------------------------------------------
    def validate_Email(self, field):
        domain = current_app.config["STUDENT_EMAIL_DOMAIN"]
        if domain and not (field.data or "").endswith("@" + domain):
            raise ValidationError(f"Use your DUT student email, for example 22226534@{domain}.")
        taken = db.session.scalar(select(User.UserId).where(func.lower(User.Email) == field.data))
        if taken:
            raise ValidationError("An account with this email already exists. Try signing in instead.")

    def validate_SAIdNumber(self, field):
        # Any 13 digits are accepted (no date, citizenship or check-digit rules).
        digits = clean_sa_id(field.data)
        if not (len(digits) == 13 and digits.isascii() and digits.isdigit()):
            raise ValidationError("ID number must be exactly 13 digits.")
        try:  # best effort: date of birth and gender when the number happens to be a real SA ID
            self.sa_id_info = parse_sa_id(digits)
        except SAIdError:
            self.sa_id_info = None
        from app.services.accounts import sa_id_fingerprint

        if db.session.scalar(select(User.UserId).where(User.SAIdHash == sa_id_fingerprint(field.data))):
            raise ValidationError("An account with this ID number already exists.")

    def validate_ResidentialAddress(self, field):
        residence = get_residence(self.Residence.data)
        if not field.data and residence:
            field.data = residence.full_address  # residence picked but address left empty
        if not field.data:
            raise ValidationError("Enter your residential address, or pick it on the map.")

    # --- helpers used by the route --------------------------------------------------
    @property
    def cleaned_id(self) -> str:
        return clean_sa_id(self.SAIdNumber.data)

    @property
    def coordinates(self):
        return parse_coordinates(self.Latitude.data, self.Longitude.data)

    @property
    def residence_is_other(self) -> bool:
        return self.Residence.data in ("", OTHER_ACCOMMODATION)


class LoginForm(FlaskForm):
    Email = StringField(
        "Email address",
        filters=[lambda v: v.strip().lower() if isinstance(v, str) else v],
        validators=[DataRequired("Enter your email address.")],
    )
    Password = PasswordField("Password", validators=[DataRequired("Enter your password.")])
    Remember = BooleanField("Keep me signed in on this device")
