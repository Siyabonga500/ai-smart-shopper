"""Field validators shared by the registration and profile forms."""

import unicodedata

from flask import current_app
from wtforms.validators import ValidationError

from app.utils.geo import is_valid_coordinate
from app.utils.validators import (
    clean_phone_digits,
    validate_email,
    validate_password,
    validate_sa_phone,
)

_NAME_EXTRA = set(" '’-.")


def normalise_text(value):
    """NFC-normalise and strip; keeps names typed with combining accents intact."""
    return unicodedata.normalize("NFC", value).strip() if isinstance(value, str) else value


def name_field(label: str):
    """Validator factory: 2-50 characters, letters plus space, apostrophe, hyphen and full stop."""

    def _validate(_form, field):
        value = normalise_text(field.data) or ""
        if not 2 <= len(value) <= 50:
            raise ValidationError(f"{label} must be between 2 and 50 characters.")
        if not value[0].isalpha() or not all(ch.isalpha() or ch in _NAME_EXTRA for ch in value):
            raise ValidationError(f"{label} can only contain letters, spaces, hyphens and apostrophes.")

    return _validate


def _wrap(check, *args):
    """Run one of the ``validate_*`` helpers and turn its ``ValueError`` into a form error."""
    try:
        return check(*args)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None


def cellphone_10_digits(_form, field):
    """Exactly 10 digits starting 06/07/08, e.g. 0821234567 (spaces and dashes are ignored)."""
    _wrap(validate_sa_phone, field.data)


def password_policy(_form, field):
    """At least 8 characters with an upper-case and a lower-case letter, a digit and a symbol."""
    _wrap(validate_password, field.data)


def email_address(_form, field):
    """Regex check, plus a DNS lookup when ``EMAIL_DNS_CHECK`` is on."""
    _wrap(validate_email, field.data, current_app.config.get("EMAIL_DNS_CHECK", False))


def clean_cellphone(value: str | None) -> str:
    return clean_phone_digits(value)


def parse_coordinates(lat_raw, lng_raw):
    """``(lat, lng)`` floats when both are present and valid, otherwise ``None``."""
    try:
        lat, lng = float(lat_raw), float(lng_raw)
    except (TypeError, ValueError):
        return None
    if lat != lat or lng != lng or not is_valid_coordinate(lat, lng):  # NaN check + range
        return None
    return lat, lng
