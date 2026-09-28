"""Move the admin's catalogue work between computers through Git: ``flask export-catalogue`` / ``flask import-catalogue``.

The database (``dev.db``) and uploaded files (``app/static/uploads/``) are never committed: they hold student
accounts and passwords. This module writes what an admin *built* into one JSON file that can be committed
(``data/catalogue.json`` by default), and reads it back on another machine:

* stores (all of them, with any edits and added branches)
* products added in Admin > Products, with their uploaded pictures embedded (base64), so they need no upload folder
* pictures pasted for barcodes (Admin > Pictures)
* edits and deletions of the built-in products (Admin > Built-in)
* the courses with their questions and answers (Admin > Courses)

Never included: students, admins, passwords, budgets, lists, history, messages, API keys.
Import is safe to run again: rows are matched by their id / slug / barcode and updated, never duplicated.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.models import (
    Answer,
    CatalogueProduct,
    Course,
    MockProductOverride,
    MockRetailerOverride,
    ProductPicture,
    Question,
    Store,
)
from app.services.photos import PRODUCT_PREFIX, detect_image_extension

FORMAT_VERSION = 1
DEFAULT_PATH = "data/catalogue.json"
UPLOAD_URL_PREFIX = "/static/" + PRODUCT_PREFIX  # "/static/uploads/products/"

STORE_FIELDS = (
    "Slug",
    "Name",
    "Brand",
    "Suburb",
    "Address",
    "Latitude",
    "Longitude",
    "LocationSource",
    "Phone",
    "OpeningHours",
    "StoreType",
)
PRODUCT_FIELDS = (
    "ProductId",
    "Category",
    "Name",
    "Brand",
    "Sku",
    "Barcode",
    "Price",
    "SalePrice",
    "ImageUrl",
    "Photos",
    "Size",
    "Colour",
    "StockStatus",
    "StockQuantity",
    "CreatedBy",
)


class TransferError(ValueError):
    """The file cannot be read or is not a catalogue export; the message is safe to show."""


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    return value


def _upload_path(url: str) -> Path | None:
    """The file on disk behind a ``/static/uploads/products/<name>`` address (only that folder, never anything else)."""
    if not isinstance(url, str) or not url.startswith(UPLOAD_URL_PREFIX):
        return None
    name = Path(url[len(UPLOAD_URL_PREFIX) :]).name
    return Path(current_app.config["UPLOAD_FOLDER"]) / "products" / name


# ---------------------------------------------------------------------------------------------- export
def export_data() -> dict:
    files: dict[str, str] = {}
    missing: list[str] = []

    def keep_file(url):
        path = _upload_path(url)
        if path is None:
            return
        if path.is_file():
            files[path.name] = base64.b64encode(path.read_bytes()).decode("ascii")
        else:
            missing.append(url)

    stores = [{f: getattr(s, f) for f in STORE_FIELDS} for s in db.session.scalars(select(Store).order_by(Store.Slug))]
    products = []
    for p in db.session.scalars(select(CatalogueProduct).order_by(CatalogueProduct.ProductId)):
        row = {f: _plain(getattr(p, f)) for f in PRODUCT_FIELDS}
        row["StoreSlug"] = p.store.Slug
        products.append(row)
        for url in p.photos:
            keep_file(url)
    pictures = [
        {"Barcode": r.Barcode, "Url": r.Url, "Position": r.Position}
        for r in db.session.scalars(select(ProductPicture).order_by(ProductPicture.Barcode, ProductPicture.Position))
    ]
    builtin = [
        {
            "Barcode": r.Barcode,
            "Hidden": r.Hidden,
            "Name": r.Name,
            "Brand": r.Brand,
            "Category": r.Category,
            "Price": _plain(r.Price),
            "UpdatedBy": r.UpdatedBy,
        }
        for r in db.session.scalars(select(MockProductOverride).order_by(MockProductOverride.Barcode))
    ]
    chains = [
        {
            "Barcode": r.Barcode,
            "Retailer": r.Retailer,
            "Listed": r.Listed,
            "Price": _plain(r.Price),
            "InStock": r.InStock,
        }
        for r in db.session.scalars(
            select(MockRetailerOverride).order_by(MockRetailerOverride.Barcode, MockRetailerOverride.Retailer)
        )
    ]
    courses = [
        {
            "Slug": c.Slug,
            "Title": c.Title,
            "Summary": c.Summary,
            "Content": c.Content,
            "Minutes": c.Minutes,
            "Icon": c.Icon,
            "Position": c.Position,
            "IsPublished": c.IsPublished,
            "questions": [
                {
                    "Text": q.Text,
                    "Explanation": q.Explanation,
                    "answers": [{"Text": a.Text, "IsCorrect": a.IsCorrect} for a in q.answers],
                }
                for q in c.questions
            ],
        }
        for c in db.session.scalars(select(Course).order_by(Course.Position))
    ]
    return {
        "format": "ai-smart-shopper-catalogue",
        "version": FORMAT_VERSION,
        "exported_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stores": stores,
        "products": products,
        "pictures": pictures,
        "builtin_products": builtin,
        "builtin_chains": chains,
        "courses": courses,
        "files": files,
        "missing_files": missing,
    }


def write_export(path: str | Path) -> dict:
    data = export_data()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return summary(data)


def summary(data: dict) -> dict:
    return {
        "stores": len(data.get("stores", [])),
        "products": len(data.get("products", [])),
        "pictures": len(data.get("pictures", [])),
        "builtin_edits": len(data.get("builtin_products", [])) + len(data.get("builtin_chains", [])),
        "courses": len(data.get("courses", [])),
        "files": len(data.get("files", {})),
        "missing_files": len(data.get("missing_files", [])),
    }


# ---------------------------------------------------------------------------------------------- import
def read_file(path: str | Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TransferError(
            f"{path} does not exist. Run `flask export-catalogue` on the computer that has the data."
        ) from None
    except (OSError, ValueError) as exc:
        raise TransferError(f"{path} is not a readable catalogue export ({exc}).") from None
    if not isinstance(data, dict) or data.get("format") != "ai-smart-shopper-catalogue":
        raise TransferError(f"{path} is not an AI Smart Shopper catalogue export.")
    if data.get("version", 0) > FORMAT_VERSION:
        raise TransferError("This export was made by a newer version of the app. Update the code first.")
    return data


def _restore_files(files: dict) -> dict[str, str]:
    """Write the embedded pictures into the upload folder. Returns ``{old name: new name}`` (normally the same)."""
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "products"
    folder.mkdir(parents=True, exist_ok=True)
    names = {}
    for name, encoded in (files or {}).items():
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError):
            continue
        extension = detect_image_extension(content[:16])
        if extension is None:
            continue  # only real JPG / PNG / WEBP pictures are written
        safe = Path(str(name)).name
        if not safe or Path(safe).suffix.lstrip(".").lower() != extension:
            safe = f"{uuid.uuid4().hex}.{extension}"
        (folder / safe).write_bytes(content)
        names[str(name)] = safe
    return names


def _renamed(url, names):
    if isinstance(url, str) and url.startswith(UPLOAD_URL_PREFIX):
        old = url[len(UPLOAD_URL_PREFIX) :]
        return UPLOAD_URL_PREFIX + names.get(old, old)
    return url


def _decimal(value):
    return None if value in (None, "") else Decimal(str(value))


def import_data(data: dict) -> dict:
    """Load an export into this database (the caller commits). Returns what was added or updated."""
    names = _restore_files(data.get("files", {}))
    counts = {"stores": 0, "products": 0, "pictures": 0, "builtin_edits": 0, "courses": 0, "files": len(names)}

    stores_by_slug = {s.Slug: s for s in db.session.scalars(select(Store))}
    for row in data.get("stores", []):
        store = stores_by_slug.get(row.get("Slug"))
        if store is None:
            store = Store(Slug=row["Slug"])
            db.session.add(store)
            stores_by_slug[store.Slug] = store
        for field in STORE_FIELDS[1:]:
            if field in row and (row[field] is not None or field in ("Suburb", "Phone", "OpeningHours")):
                setattr(store, field, row[field])
        counts["stores"] += 1
    db.session.flush()

    for row in data.get("products", []):
        store = stores_by_slug.get(row.get("StoreSlug"))
        if store is None:
            continue  # its store is not in the file or the database
        product = db.session.get(CatalogueProduct, row.get("ProductId")) if row.get("ProductId") else None
        if product is None:
            product = CatalogueProduct(ProductId=row.get("ProductId") or None)
            db.session.add(product)
        for field in PRODUCT_FIELDS[1:]:
            value = row.get(field)
            if field in ("Price", "SalePrice"):
                value = _decimal(value)
            elif field == "ImageUrl":
                value = _renamed(value, names)
            elif field == "Photos":
                value = [_renamed(u, names) for u in value] if isinstance(value, list) else None
            setattr(product, field, value)
        product.StoreId = store.StoreId
        counts["products"] += 1

    by_barcode: dict[str, list] = {}
    for row in data.get("pictures", []):
        by_barcode.setdefault(row["Barcode"], []).append(row)
    for barcode, rows in by_barcode.items():
        db.session.query(ProductPicture).filter(ProductPicture.Barcode == barcode).delete()
        for row in sorted(rows, key=lambda r: r.get("Position", 0)):
            db.session.add(ProductPicture(Barcode=barcode, Url=row["Url"], Position=row.get("Position", 0)))
            counts["pictures"] += 1

    for row in data.get("builtin_products", []):
        override = db.session.get(MockProductOverride, row["Barcode"]) or MockProductOverride(Barcode=row["Barcode"])
        db.session.add(override)
        override.Hidden = bool(row.get("Hidden"))
        override.Name, override.Brand, override.Category = row.get("Name"), row.get("Brand"), row.get("Category")
        override.Price, override.UpdatedBy = _decimal(row.get("Price")), row.get("UpdatedBy")
        counts["builtin_edits"] += 1
    for row in data.get("builtin_chains", []):
        chain = (
            db.session.query(MockRetailerOverride)
            .filter(MockRetailerOverride.Barcode == row["Barcode"], MockRetailerOverride.Retailer == row["Retailer"])
            .one_or_none()
        ) or MockRetailerOverride(Barcode=row["Barcode"], Retailer=row["Retailer"])
        db.session.add(chain)
        chain.Listed, chain.Price, chain.InStock = row.get("Listed"), _decimal(row.get("Price")), row.get("InStock")
        counts["builtin_edits"] += 1

    for row in data.get("courses", []):
        course = db.session.scalar(select(Course).where(Course.Slug == row["Slug"]))
        if course is None:
            course = Course(Slug=row["Slug"])
            db.session.add(course)
        for field in ("Title", "Summary", "Content", "Minutes", "Icon", "Position", "IsPublished"):
            if field in row:
                setattr(course, field, row[field])
        wanted = [
            (q["Text"], q.get("Explanation"), [(a["Text"], bool(a.get("IsCorrect"))) for a in q.get("answers", [])])
            for q in row.get("questions", [])
        ]
        have = [(q.Text, q.Explanation, [(a.Text, bool(a.IsCorrect)) for a in q.answers]) for q in course.questions]
        counts["courses"] += 1
        if wanted == have:
            continue  # unchanged: keep the questions (and the students' past answers to them)
        course.questions.clear()  # the file's questions replace the old ones (attempts keep their scores)
        db.session.flush()
        for q_pos, q in enumerate(row.get("questions", []), 1):
            question = Question(Text=q["Text"], Explanation=q.get("Explanation"), Position=q_pos)
            for a_pos, a in enumerate(q.get("answers", []), 1):
                question.answers.append(Answer(Text=a["Text"], IsCorrect=bool(a.get("IsCorrect")), Position=a_pos))
            course.questions.append(question)
    return counts
