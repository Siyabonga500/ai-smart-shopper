"""Primary-key generation.

Every model uses a ``db.String(30)`` primary key (Budget Shopper Views Plan
PDF, pages 18-22), so IDs are generated in Python as ``<PREFIX>-<UUID hex>``,
e.g. ``USR-3f9a1c0e5b7d4e2a8c6f1b0d2a``.

A full UUID (32 hex characters) plus a prefix cannot fit in 30 characters, so
the UUID4 hex string is truncated to whatever room the prefix leaves. With a
3-letter prefix that is 26 hex characters (about 98 random bits), which is far
beyond any collision risk for this app.
"""

import uuid

MAX_ID_LENGTH = 30
_MIN_HEX_CHARS = 16

# One prefix per model.
USER_PREFIX = "USR"
BUDGET_PREFIX = "BGT"
SUB_BUDGET_PREFIX = "SBG"
SHOPPING_LIST_PREFIX = "LST"
LIST_ITEM_PREFIX = "ITM"
PREFERENCE_PREFIX = "PRF"
STORE_PREFIX = "STR"
NOTIFICATION_PREFIX = "NTF"
MAPPING_PREFIX = "CMP"
AUDIT_PREFIX = "AUD"
PRODUCT_PREFIX = "PRD"
CONTACT_PREFIX = "MSG"
COURSE_PREFIX = "CRS"
QUESTION_PREFIX = "QST"
ANSWER_PREFIX = "ANS"
ATTEMPT_PREFIX = "ATT"


def generate_id(prefix: str) -> str:
    """Return a unique prefixed UUID string such as ``BGT-...`` (max 30 characters)."""
    prefix = prefix.upper()
    hex_chars = MAX_ID_LENGTH - len(prefix) - 1  # -1 for the dash
    if hex_chars < _MIN_HEX_CHARS:
        raise ValueError(
            f"ID prefix {prefix!r} is too long: at least {_MIN_HEX_CHARS} UUID characters "
            f"must fit inside {MAX_ID_LENGTH} characters"
        )
    return f"{prefix}-{uuid.uuid4().hex[:hex_chars]}"
