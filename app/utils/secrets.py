"""Encrypting the API keys an admin types into the portal.

The keys are stored encrypted (Fernet: AES-128-CBC with an HMAC) so a copy of the database, or a database
backup, does not contain a usable Apify token or Parse.bot key. The encryption key comes from
``SETTINGS_ENCRYPTION_KEY`` when it is set, otherwise it is derived from ``SECRET_KEY``. Changing that secret
makes the stored keys unreadable; :func:`decrypt_secret` then returns ``None`` (never raises) and the admin is
asked to type the key again.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

_PURPOSE = b"ai-smart-shopper:integration-keys:v1"


def _fernet() -> Fernet:
    secret = current_app.config.get("SETTINGS_ENCRYPTION_KEY") or current_app.config["SECRET_KEY"]
    digest = hashlib.sha256(_PURPOSE + b"|" + str(secret).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str | None) -> str | None:
    """The original text, or ``None`` when there is nothing stored or it can no longer be decrypted."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None


def mask_secret(value: str | None) -> str:
    """``••••••••wxyz``: enough to recognise a key, never enough to use it. Short keys show no characters."""
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 12 else ""
    return "•" * 8 + tail
