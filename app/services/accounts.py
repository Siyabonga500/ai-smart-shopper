"""Account creation helpers shared by the routes."""

from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.models import User
from app.utils.sa_id import hash_sa_id, last_four
from app.utils.validators import validate_sa_phone


def sa_id_fingerprint(id_number: str) -> str:
    """HMAC fingerprint of an SA ID (the number itself is never stored)."""
    pepper = current_app.config.get("ID_HASH_PEPPER") or current_app.config["SECRET_KEY"]
    return hash_sa_id(id_number, pepper)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_user_from_registration(form, microsoft_id: str | None = None) -> User:
    """Create (but do not commit) a ``User`` from a validated ``RegistrationForm``."""
    now = utcnow()
    info = form.sa_id_info
    user = User(
        FirstName=form.FirstName.data,
        LastName=form.LastName.data,
        Email=form.Email.data,
        MicrosoftId=microsoft_id,
        CellphoneNumber=validate_sa_phone(form.CellphoneNumber.data),
        ResidentialAddress=form.ResidentialAddress.data,
        SAIdHash=sa_id_fingerprint(form.cleaned_id),
        SAIdLast4=last_four(form.cleaned_id),
        DateOfBirth=info.date_of_birth if info else None,
        Gender=form.Gender.data or (info.gender if info else None),
        Race=form.Race.data,
        TermsAcceptedOn=now,
        CreatedOn=now,
        LastLogin=now,
    )
    if not form.microsoft:
        user.set_password(form.Password.data)
    db.session.add(user)
    return user


def apply_location(user: User, coordinates, address: str | None, address_changed: bool = True) -> None:
    """Store the map position for ``user``'s address.

    * a pin from the browser wins;
    * otherwise, when the address is new, look it up on the server (best effort, see GEOCODE_ON_SAVE);
    * an unchanged address keeps its cached position.
    """
    from app.services.geocoding import coordinates_for_address  # local import: avoids a cycle at start-up

    if coordinates:
        user.Latitude, user.Longitude = coordinates
    elif address_changed:
        found = coordinates_for_address(address) if (address and current_app.config.get("GEOCODE_ON_SAVE")) else None
        user.Latitude, user.Longitude = found if found else (None, None)
