"""Small security helpers."""

from urllib.parse import urlparse


def is_safe_redirect_url(target: str | None, host: str | None = None) -> bool:
    """True only for same-site relative paths (blocks open redirects such as ``//evil.com``)."""
    if not target:
        return False
    if any(char in target for char in ("\\", "\r", "\n", "\t")):
        return False
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        # Absolute URLs are allowed only when they point back at this host.
        return bool(host) and parsed.netloc == host and parsed.scheme in ("http", "https")
    return target.startswith("/") and not target.startswith("//")
