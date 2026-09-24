"""Profile picture storage.

Only the file's own bytes decide whether it is an image (JPEG, PNG or WEBP signature) - the extension
the browser reports is ignored, and the stored name is random, so nothing the user types reaches the
file system. SVG is not accepted (it can carry scripts). Files live in ``UPLOAD_FOLDER/profile`` and the
database keeps ``uploads/profile/<name>`` (relative to ``static/``).
"""

import uuid
from pathlib import Path

from flask import current_app

PUBLIC_PREFIX = "uploads/profile/"


class PhotoError(ValueError):
    """The uploaded file cannot be used; the message is safe to show to the student."""


def detect_image_extension(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def _folder() -> Path:
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "profile"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_profile_photo(file_storage) -> str:
    """Validate and store an uploaded photo; returns the value for ``User.ProfilePic``."""
    limit = current_app.config["MAX_UPLOAD_BYTES"]
    data = file_storage.stream.read(limit + 1)
    if not data:
        raise PhotoError("The photo you chose is empty.")
    if len(data) > limit:
        raise PhotoError(f"Photo must be {limit // (1024 * 1024)}MB or smaller.")
    extension = detect_image_extension(data[:16])
    if extension is None:
        raise PhotoError("That file is not a JPG, PNG or WEBP image.")

    name = f"{uuid.uuid4().hex}.{extension}"
    (_folder() / name).write_bytes(data)
    return PUBLIC_PREFIX + name


def delete_profile_photo(profile_pic: str | None) -> None:
    """Remove a stored photo. Anything that is not one of our own paths is ignored."""
    if not profile_pic or not profile_pic.startswith(PUBLIC_PREFIX):
        return
    name = Path(profile_pic).name  # never follow directories in the stored value
    try:
        (Path(current_app.config["UPLOAD_FOLDER"]) / "profile" / name).unlink(missing_ok=True)
    except OSError:
        current_app.logger.warning("Could not delete old profile photo %s", name)


PRODUCT_PREFIX = "uploads/products/"


def save_product_photo(file_storage) -> str:
    """Validate and store a product photo from Admin > Products; returns ``/static/uploads/products/<name>``."""
    limit = current_app.config["MAX_UPLOAD_BYTES"]
    data = file_storage.stream.read(limit + 1)
    if not data:
        raise PhotoError(f"The photo {file_storage.filename or ''} is empty.".replace("  ", " "))
    if len(data) > limit:
        raise PhotoError(f"Each photo must be {limit // (1024 * 1024)}MB or smaller.")
    extension = detect_image_extension(data[:16])
    if extension is None:
        raise PhotoError("Photos must be JPG, PNG or WEBP images.")
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "products"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{extension}"
    (folder / name).write_bytes(data)
    return "/static/" + PRODUCT_PREFIX + name
