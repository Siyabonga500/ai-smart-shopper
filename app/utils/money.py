"""Money helpers. All amounts are ZAR and stored as ``Numeric(10, 2)``."""

from decimal import Decimal, InvalidOperation

ZERO = Decimal("0.00")


def to_decimal(value) -> Decimal:
    """Coerce ``None`` / int / float / str / Decimal to ``Decimal``.

    Floats go through ``str`` so ``0.1`` becomes ``Decimal("0.1")`` rather than
    the binary approximation. Unparseable input is treated as zero.
    """
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return ZERO


# --- parsing what a student types into an amount box -------------------------------------------------
import re  # noqa: E402

_THOUSANDS_COMMAS = re.compile(r"^\d{1,3}(,\d{3})+(\.\d{1,2})?$", re.ASCII)  # ASCII digits only, as in main.js
_PLAIN = re.compile(r"^\d+([.,]\d{1,2})?$", re.ASCII)


def parse_amount(text) -> Decimal | None:
    """Parse ``"1500"``, ``"R1 500"``, ``"1,500.50"``, ``"29,99"`` into a Decimal, or ``None`` if it is not a number.

    Spaces (also non-breaking) and a leading ``R`` are ignored. A comma is a thousands separator when three
    digits follow it (``1,500``) and a decimal comma otherwise (``29,99``). Negative numbers, exponents and
    anything else are rejected. static/js/main.js has the same rules (``App.parseAmount``).
    """
    if text is None:
        return None
    cleaned = re.sub(r"[\s ]", "", str(text))
    if cleaned[:1] in ("R", "r"):
        cleaned = cleaned[1:]
    if _THOUSANDS_COMMAS.match(cleaned):
        cleaned = cleaned.replace(",", "")
    elif _PLAIN.match(cleaned):
        cleaned = cleaned.replace(",", ".")
    else:
        return None
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def money(value) -> Decimal:
    """``value`` as a Decimal rounded to cents (half up)."""
    from decimal import ROUND_HALF_UP

    return to_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
