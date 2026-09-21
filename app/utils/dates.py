"""Date helpers. Timestamps are stored in UTC; everything the student sees and every "this month" rule uses
South African time (SAST, UTC+2, no daylight saving).

Month names are spelled out here rather than taken from ``strftime('%b')`` so the output does not change with
the server's locale.
"""

from datetime import date, datetime, timedelta, timezone

from app.utils.formatters import SAST

MONTHS_SHORT = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTHS_LONG = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_sast(value: datetime | None) -> datetime | None:
    """Convert a stored timestamp (SQLite returns naive UTC) to aware SAST; ``None`` stays ``None``."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SAST)


def as_utc(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def today_sast(now: datetime | None = None) -> date:
    return (to_sast(now) or to_sast(utcnow())).date()


def default_budget_title(today: date | None = None) -> str:
    """``06 Sep 2026 Budget`` - the suggested title on the Set Budget page."""
    today = today or today_sast()
    return f"{today.day:02d} {MONTHS_SHORT[today.month - 1]} {today.year} Budget"


def month_key(value: datetime | date | None) -> tuple[int, int] | None:
    """``(year, month)`` in South African time, or ``None``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = to_sast(value)
    return (value.year, value.month)


def previous_months(today: date, count: int) -> list[tuple[int, int]]:
    """The last ``count`` months ending with ``today``'s month, oldest first: ``[(2026, 4), ..., (2026, 9)]``."""
    year, month = today.year, today.month
    months = []
    for _ in range(count):
        months.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(months))


def month_label(key: tuple[int, int], long: bool = False) -> str:
    year, month = key
    return f"{MONTHS_LONG[month - 1]} {year}" if long else MONTHS_SHORT[month - 1]


_EARLIEST, _LATEST = date(1900, 1, 1), date(2200, 12, 31)


def sast_day_bounds(start: date | None, end: date | None) -> tuple[datetime | None, datetime | None]:
    """UTC ``[start, end)`` for inclusive SAST calendar dates (either may be ``None``); for date-range filters."""
    # Dates come from a URL; year 1 or 9999 would overflow the time-zone maths, so keep them in a sane range.
    start = min(max(start, _EARLIEST), _LATEST) if start else None
    end = min(max(end, _EARLIEST), _LATEST) if end else None
    lower = datetime(start.year, start.month, start.day, tzinfo=SAST).astimezone(timezone.utc) if start else None
    upper = None
    if end:
        upper = (datetime(end.year, end.month, end.day, tzinfo=SAST) + timedelta(days=1)).astimezone(timezone.utc)
    return lower, upper
