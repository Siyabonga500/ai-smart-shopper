"""Display helpers. All money in the app is ZAR."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

NBSP = " "


def format_zar(value, cents="auto", symbol: str = "R") -> str:
    """Format an amount the way the PDF mock-ups do.

    ``R1 750`` (whole amounts drop the cents), ``R29.99``, ``-R72.88``.
    A non-breaking space is used as the thousands separator so amounts never
    wrap across lines.

    ``cents``: ``"auto"`` (default), ``True`` (always 2 decimals) or ``False``
    (never; rounds to whole rands).
    """
    try:
        amount = Decimal(str(value if value is not None else 0))
    except InvalidOperation:
        amount = Decimal(0)

    if cents is False:
        amount = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    else:
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    negative = amount < 0
    amount = abs(amount)
    show_cents = cents is True or (cents == "auto" and amount != amount.to_integral_value())

    body = f"{amount:,.2f}" if show_cents else f"{amount:,.0f}"
    body = body.replace(",", NBSP)
    return f"{'-' if negative else ''}{symbol}{body}"


# --- dates (stored in UTC, shown in South African time) ---------------------------------------
from datetime import datetime, timedelta, timezone  # noqa: E402

SAST = timezone(timedelta(hours=2), "SAST")  # South Africa has no daylight saving


def _to_sast(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:  # SQLite hands back naive UTC datetimes
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SAST)


def format_datetime(value, empty: str = "—") -> str:
    """``20 Sep 2026, 21:16`` in South African time."""
    local = _to_sast(value)
    return local.strftime("%d %b %Y, %H:%M") if local else empty


def format_date(value, empty: str = "—") -> str:
    """``20 Sep 2026`` in South African time."""
    local = _to_sast(value)
    return local.strftime("%d %b %Y") if local else empty


MINUS = "−"


def signed_zar(value, cents="auto") -> str:
    """``+R700`` / ``−R80`` / ``R0`` - a difference against a reference amount (Budget view, section D)."""
    try:
        amount = Decimal(str(value if value is not None else 0))
    except InvalidOperation:
        amount = Decimal(0)
    body = format_zar(abs(amount), cents=cents)
    if amount > 0:
        return "+" + body
    if amount < 0:
        return MINUS + body
    return body


def zar_parts(value) -> tuple[str, str]:
    """``("29", "99")`` for R29.99 - the whole rands (with thousands separators) and the cents - so a template can
    print the cents small and raised, like the mock-ups (R29⁹⁹). Cents are ``""`` for whole amounts."""
    text = format_zar(abs(Decimal(str(value or 0))), cents="auto", symbol="")
    whole, _, cents = text.partition(".")
    return whole, cents
