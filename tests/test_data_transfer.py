"""Moving the catalogue between computers: export to one committable file, import on a fresh database."""

import io
import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import (
    CatalogueProduct,
    Course,
    MockProductOverride,
    MockRetailerOverride,
    ProductPicture,
    Store,
    User,
)
from app.services import admin_stores, data_transfer
from app.services import courses as course_service

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
D = Decimal


@pytest.fixture
def uploads(app, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    return tmp_path / "uploads"


def build_catalogue(uploads, make_user):
    admin_stores.add_known_stores()
    store = db.session.scalar(select(Store).where(Store.Slug == "mrprice-workshop"))
    store.Phone = "031 000 1111"  # an admin edit to a store
    (uploads / "products").mkdir(parents=True)
    (uploads / "products" / "abc123.png").write_bytes(PNG)
    db.session.add(
        CatalogueProduct(
            ProductId="PRD-test1",
            Category="Clothing",
            Name="Golfer",
            Brand="RJ",
            Sku="RJ40",
            Price=D("300"),
            SalePrice=D("250"),
            ImageUrl="/static/uploads/products/abc123.png",
            Photos=["/static/uploads/products/abc123.png", "https://img.example.com/back.jpg"],
            Size="S",
            Colour="White",
            StockStatus="in_stock",
            StockQuantity=4,
            StoreId=store.StoreId,
        )
    )
    db.session.add(ProductPicture(Barcode="2000000000275", Url="https://img.example.com/milk.jpg", Position=1))
    db.session.add(MockProductOverride(Barcode="2000000000275", Hidden=False, Price=D("40")))
    db.session.add(MockRetailerOverride(Barcode="2000000000275", Retailer="Checkers", Price=D("19.99")))
    make_user(email="student@dut4life.ac.za")  # must never be exported
    course_service.ensure_courses()
    db.session.commit()


def wipe():
    for model in (CatalogueProduct, ProductPicture, MockRetailerOverride, MockProductOverride, Course, Store):
        for row in db.session.scalars(select(model)):
            db.session.delete(row)
    db.session.commit()


def test_export_then_import_on_an_empty_database(app, uploads, make_user, tmp_path):
    build_catalogue(uploads, make_user)
    path = tmp_path / "data" / "catalogue.json"
    counts = data_transfer.write_export(path)
    assert counts["products"] == 1 and counts["files"] == 1 and counts["pictures"] == 1 and counts["courses"] == 3
    text = path.read_text(encoding="utf-8")
    assert "student@dut4life.ac.za" not in text and "PasswordHash" not in text  # never any accounts

    # "another computer": no products, no stores, no uploaded files
    wipe()
    (uploads / "products" / "abc123.png").unlink()
    counts = data_transfer.import_data(data_transfer.read_file(path))
    db.session.commit()
    assert counts["files"] == 1 and (uploads / "products" / "abc123.png").read_bytes() == PNG

    product = db.session.get(CatalogueProduct, "PRD-test1")
    assert product.Name == "Golfer" and product.SalePrice == D("250") and product.store.Slug == "mrprice-workshop"
    assert product.photos == ["/static/uploads/products/abc123.png", "https://img.example.com/back.jpg"]
    assert db.session.scalar(select(Store).where(Store.Slug == "mrprice-workshop")).Phone == "031 000 1111"
    assert db.session.scalar(select(ProductPicture)).Url == "https://img.example.com/milk.jpg"
    assert db.session.get(MockProductOverride, "2000000000275").Price == D("40")
    assert db.session.scalar(select(MockRetailerOverride)).Price == D("19.99")
    assert db.session.query(Course).count() == 3
    assert db.session.scalar(select(User).where(User.Email == "student@dut4life.ac.za")) is not None  # untouched


def test_importing_twice_duplicates_nothing(app, uploads, make_user, tmp_path):
    build_catalogue(uploads, make_user)
    path = tmp_path / "catalogue.json"
    data_transfer.write_export(path)
    for _ in range(2):
        data_transfer.import_data(data_transfer.read_file(path))
        db.session.commit()
    assert db.session.query(CatalogueProduct).count() == 1
    assert db.session.query(ProductPicture).count() == 1
    assert db.session.query(MockRetailerOverride).count() == 1
    assert db.session.query(Course).count() == 3


def test_bad_files_are_refused(tmp_path):
    with pytest.raises(data_transfer.TransferError, match="does not exist"):
        data_transfer.read_file(tmp_path / "nope.json")
    other = tmp_path / "other.json"
    other.write_text('{"hello": 1}', encoding="utf-8")
    with pytest.raises(data_transfer.TransferError, match="not an AI Smart Shopper"):
        data_transfer.read_file(other)


def test_cli_commands(app, uploads, make_user, tmp_path):
    build_catalogue(uploads, make_user)
    runner = app.test_cli_runner()
    path = str(tmp_path / "c.json")
    out = runner.invoke(args=["export-catalogue", path])
    assert out.exit_code == 0 and "1 products" in out.output
    wipe()
    out = runner.invoke(args=["import-catalogue", path])
    assert out.exit_code == 0 and "1 products" in out.output
    assert db.session.query(CatalogueProduct).count() == 1
    missing = runner.invoke(args=["import-catalogue", str(tmp_path / "none.json"), "--if-present"])
    assert missing.exit_code == 0 and "nothing to import" in missing.output


def test_admin_backup_page_downloads_and_loads(client, make_user, login, uploads, tmp_path):
    build_catalogue(uploads, make_user)
    login(make_user(email="boss@example.com", IsAdmin=True))
    assert "Download catalogue.json" in client.get("/admin/backup").get_data(as_text=True)
    download = client.get("/admin/backup?download=1")
    assert download.status_code == 200 and "attachment" in download.headers["Content-Disposition"]
    exported = download.get_data()
    wipe()
    response = client.post(
        "/admin/backup",
        data={"file": (io.BytesIO(exported), "catalogue.json")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "Loaded 1 products" in response.get_data(as_text=True)
    assert db.session.query(CatalogueProduct).count() == 1
    bad = client.post(
        "/admin/backup",
        data={"file": (io.BytesIO(b"{}"), "x.json")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "not an AI Smart Shopper catalogue" in bad.get_data(as_text=True)
    assert json.loads(exported)["files"]
