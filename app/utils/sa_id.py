"""South African ID number helpers.

A 13-digit SA ID is laid out as ``YYMMDD SSSS C A Z``:

    YYMMDD  digits 0-5   date of birth
    SSSS    digits 6-9   gender sequence: 0000-4999 female, 5000-9999 male
    C       digit 10     citizenship: 0 = SA citizen, 1 = permanent resident
    A       digit 11     legacy digit (8 or 9 on modern IDs)
    Z       digit 12     Luhn check digit

Note the date of birth comes from digits 0-5 but gender comes from digits 6-9.

Privacy: the app never stores the full number. It keeps an HMAC fingerprint
(to stop the same ID registering twice) and the last four digits (so the
profile can show a masked value).
"""

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import date

_ID_RE = re.compile(
    r"^[0-9]{13}$"
)  # ASCII digits only: "\d" would also take Arabic-Indic digits, which hash differently
_MASK = "*" * 9


class SAIdError(ValueError):
    """Raised with a user-friendly message when an ID number is not valid."""


@dataclass(frozen=True)
class SAIdInfo:
    date_of_birth: date
    gender: str  # "Male" or "Female"
    is_citizen: bool  # digit 10: 0 = citizen, 1 = permanent resident


def clean_sa_id(value: str | None) -> str:
    """Strip spaces and dashes people type between digit groups."""
    return re.sub(r"[\s\-]", "", value or "")


def luhn_valid(digits: str) -> bool:
    """Luhn checksum: double every second digit from the right, sum, must be divisible by 10."""
    if not (digits.isascii() and digits.isdigit()):
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def parse_sa_id(id_number: str, today: date | None = None) -> SAIdInfo:
    """Validate ``id_number`` and derive date of birth and gender.

    The century is inferred as the most recent one that does not put the birth
    date in the future (``05`` becomes 2005, ``99`` becomes 1999).
    """
    today = today or date.today()
    id_number = clean_sa_id(id_number)
    if not _ID_RE.match(id_number):
        raise SAIdError("ID number must be exactly 13 digits.")

    yy, month, day = int(id_number[0:2]), int(id_number[2:4]), int(id_number[4:6])
    birth = None
    for year in (2000 + yy, 1900 + yy):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate <= today:
            birth = candidate
            break
    if birth is None:
        raise SAIdError("The date of birth in this ID number is not a valid date.")

    if id_number[10] not in "01":
        raise SAIdError("The citizenship digit of this ID number (the 11th digit) must be 0 or 1.")

    if not luhn_valid(id_number):
        raise SAIdError("This ID number is not valid. Please check for a typing mistake.")

    return SAIdInfo(
        date_of_birth=birth,
        gender="Male" if int(id_number[6:10]) >= 5000 else "Female",
        is_citizen=id_number[10] == "0",
    )


def hash_sa_id(id_number: str, pepper: str) -> str:
    """HMAC-SHA256 fingerprint (64 hex chars) used for duplicate detection."""
    return hmac.new(pepper.encode(), clean_sa_id(id_number).encode(), hashlib.sha256).hexdigest()


def last_four(id_number: str) -> str:
    return clean_sa_id(id_number)[-4:]


def mask_sa_id(last4: str | None) -> str:
    """``*********1234`` for display; a dash when nothing is stored."""
    return f"{_MASK}{last4}" if last4 else "—"
