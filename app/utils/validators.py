"""Reusable validation helpers (forms and services call these).

The ``validate_*`` functions are the strict, spec-level validators used by the
forms (Step 19). Each one returns the cleaned value and raises ``ValueError``
with a message that is safe to show to a student next to the field.
"""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.utils.passwords import password_problems
from app.utils.sa_id import clean_sa_id, parse_sa_id

log = logging.getLogger(__name__)

# South African mobile: 0XX XXX XXXX, 27XXXXXXXXX or +27XXXXXXXXX (mobile ranges 06x/07x/08x)
_SA_CELL_RE = re.compile(r"^(?:\+27|27|0)([678]\d{8})$", re.ASCII)


def normalise_sa_cellphone(number: str | None) -> str | None:
    """Return ``+27XXXXXXXXX`` or ``None`` when the number is not a valid SA mobile."""
    if not number:
        return None
    cleaned = re.sub(r"[\s\-()]", "", number)
    match = _SA_CELL_RE.match(cleaned)
    return f"+27{match.group(1)}" if match else None


def is_valid_sa_cellphone(number: str | None) -> bool:
    return normalise_sa_cellphone(number) is not None


# The registration / profile forms ask for the local 10-digit format only, e.g. 0821234567
_SA_MOBILE_10_RE = re.compile(r"^0[678]\d{8}$", re.ASCII)


def clean_phone_digits(number: str | None) -> str:
    """Remove the spaces, dashes and brackets people type into phone numbers."""
    return re.sub(r"[\s\-()]", "", number or "")


def is_valid_sa_mobile_10(number: str | None) -> bool:
    """Exactly 10 digits, starting 06/07/08 (``0821234567``)."""
    return bool(_SA_MOBILE_10_RE.match(clean_phone_digits(number)))


def allowed_image(filename: str | None, allowed_extensions) -> bool:
    """True when ``filename`` has one of the allowed image extensions (JPG/PNG/WEBP)."""
    if not filename or "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in allowed_extensions


def parse_zar_amount(value) -> Decimal:
    """Parse user input like ``"R1 750.50"`` into a non-negative 2-dp ``Decimal``.

    Raises ``ValueError`` for empty, non-numeric or negative input.
    """
    if value is None:
        raise ValueError("Amount is required")
    text = str(value).replace(" ", "").replace(" ", "").replace(",", "")
    if text[:1] in {"R", "r"}:
        text = text[1:]
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("Enter a valid amount in rands") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("Amount cannot be negative")
    return amount.quantize(Decimal("0.01"))


# --------------------------------------------------------------------------------------
# Step 19: strict validators
# --------------------------------------------------------------------------------------
PHONE_MESSAGE = "Enter your cellphone number as 10 digits, for example 0821234567."


def validate_sa_id(id_number: str | None, today: date | None = None) -> str:
    """Return the cleaned 13-digit ID number or raise ``ValueError``.

    Checks: exactly 13 ASCII digits, a real ``YYMMDD`` date that is not in the
    future, citizenship digit 0 or 1, and the Luhn check digit.
    """
    id_number = clean_sa_id(id_number)
    parse_sa_id(id_number, today)  # raises SAIdError (a ValueError) with a friendly message
    return id_number


def extract_dob_and_gender(id_number: str | None, today: date | None = None) -> tuple[date, str]:
    """``(date_of_birth, 'Male' | 'Female')`` from a valid ID number.

    Gender comes from digits 7-10 (0000-4999 female, 5000-9999 male); the date of
    birth from the first six digits. Raises ``ValueError`` when the ID is invalid.
    """
    info = parse_sa_id(id_number, today)
    return info.date_of_birth, info.gender


def validate_sa_phone(number: str | None) -> str:
    """Return the 10-digit local number (``0821234567``) or raise ``ValueError``.

    Exactly 10 digits, starting with 0 and then 6, 7 or 8. Spaces, dashes and
    brackets typed between the digits are ignored.
    """
    digits = clean_phone_digits(number)
    if not _SA_MOBILE_10_RE.match(digits):
        raise ValueError(PHONE_MESSAGE)
    return digits


def validate_password(password: str | None) -> str:
    """Return ``password`` unchanged or raise ``ValueError`` naming what is missing.

    Rules: at least 8 characters, one upper-case letter, one lower-case letter,
    one digit and one symbol (also capped at 72 bytes, the bcrypt limit).
    """
    problems = password_problems(password)
    if problems:
        raise ValueError("Your password needs " + ", ".join(problems) + ".")
    return password or ""


_EMAIL_LOCAL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_EMAIL_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_EMAIL_TLD_RE = re.compile(r"^(?:[A-Za-z]{2,63}|xn--[A-Za-z0-9-]{2,59})$")
EMAIL_MESSAGE = "Enter a valid email address, for example name@example.com."
DNS_TIMEOUT_SECONDS = 3.0


def validate_email(email: str | None, check_dns: bool = False) -> str:
    """Return the lower-cased email address or raise ``ValueError``.

    A practical regex check (one ``@``, a sane local part, a dotted domain with a
    letter-only top-level domain, 254 characters at most). With ``check_dns`` the
    domain must also have a mail (MX) or address (A/AAAA) record. A DNS *outage*
    (timeout, no resolver) is never held against the student: only a definite
    "this domain does not exist" answer rejects the address.
    """
    value = (email or "").strip()
    if not value or len(value) > 254 or value.count("@") != 1:
        raise ValueError(EMAIL_MESSAGE)
    local, domain = value.split("@")
    domain = domain.rstrip(".")
    try:
        domain = domain.encode("idna").decode("ascii")  # accept internationalised domains
    except UnicodeError:
        raise ValueError(EMAIL_MESSAGE) from None
    labels = domain.split(".")
    if (
        not 1 <= len(local) <= 64
        or not _EMAIL_LOCAL_RE.match(local)
        or len(labels) < 2
        or len(domain) > 253
        or not all(_EMAIL_LABEL_RE.match(label) for label in labels)
        or not _EMAIL_TLD_RE.match(labels[-1])
    ):
        raise ValueError(EMAIL_MESSAGE)
    value = f"{local}@{domain}".lower()
    if check_dns and not _domain_can_receive_mail(domain):
        raise ValueError("We could not find that email domain. Check for a typing mistake.")
    return value


def _domain_can_receive_mail(domain: str) -> bool:
    """True when DNS says the domain can take mail, or when DNS cannot be asked."""
    try:
        import dns.exception
        import dns.resolver
    except ImportError:  # dnspython is optional
        log.warning("dnspython is not installed; skipping the email DNS check")
        return True
    resolver = dns.resolver.Resolver()
    resolver.lifetime = resolver.timeout = DNS_TIMEOUT_SECONDS
    for record_type in ("MX", "A", "AAAA"):
        try:
            if len(resolver.resolve(domain, record_type)):
                return True
        except dns.resolver.NXDOMAIN:
            return False  # the domain definitely does not exist
        except dns.resolver.NoAnswer:
            continue  # no record of this type; try the next
        except (dns.exception.DNSException, OSError):
            return True  # timeout / no resolver: do not block sign-up
    return False
