"""Password policy: min 8 characters, 1 uppercase letter, 1 lowercase letter, 1 number, 1 symbol.

bcrypt only uses the first 72 bytes of a password (and newer bcrypt releases
refuse longer input), so the policy also caps the length at 72 bytes.
"""

import unicodedata

MIN_LENGTH = 8
MAX_BYTES = 72
_ASCII_DIGITS = frozenset("0123456789")


def _is_symbol(char: str) -> bool:
    """Punctuation or symbol (Unicode categories P* and S*), e.g. ! @ # $ % _ € ~. Same rule as validators.js."""
    return unicodedata.category(char)[0] in "PS"


def password_problems(password: str | None) -> list[str]:
    """Human-readable list of what is missing; empty when the password is acceptable."""
    password = password or ""
    problems = []
    if len(password) < MIN_LENGTH:
        problems.append(f"at least {MIN_LENGTH} characters")
    if not any(char.isupper() for char in password):
        problems.append("one uppercase letter")
    if not any(char.islower() for char in password):
        problems.append("one lowercase letter")
    if not any(char in _ASCII_DIGITS for char in password):
        problems.append("one number")
    if not any(_is_symbol(char) for char in password):
        problems.append("one symbol (for example ! @ # $ %)")
    if len(password.encode("utf-8")) > MAX_BYTES:
        problems.append(f"no more than {MAX_BYTES} bytes")
    return problems
