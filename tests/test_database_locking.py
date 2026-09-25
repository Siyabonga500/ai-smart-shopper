""" "database is locked": SQLite waits for other writers, uses WAL, and no page keeps a write open during price lookups."""

import sqlite3
import threading
import time
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.services import budgets, notifications, shopping
from app.services.retail_api import get_retail_provider

D = Decimal
HOME = {"lat": -29.8587, "lng": 31.0218}


class WatchingProvider:
    """Wraps the real provider and fails the test if a lookup starts while a database write is pending."""

    def __init__(self, inner):
        self.inner, self.name, self.calls = inner, inner.name, 0

    def _check(self):
        self.calls += 1
        assert not db.session.dirty and not db.session.new, "a write was pending during a price lookup"

    def search_products(self, *args, **kwargs):
        self._check()
        return self.inner.search_products(*args, **kwargs)

    def get_offers_by_barcode(self, *args, **kwargs):
        self._check()
        return self.inner.get_offers_by_barcode(*args, **kwargs)


@pytest.fixture
def list_with_items(make_user):
    user = make_user(Latitude=HOME["lat"], Longitude=HOME["lng"])
    budgets.create_budget(user.UserId, "Sep", [("Grocery", D("900"))])
    provider = get_retail_provider()
    for barcode in ("2000000000275", "2000000000305", "2000000000015", "2000000000022"):
        offers = [o for o in provider.get_offers_by_barcode(barcode, HOME["lat"], HOME["lng"], 15) if o.in_stock]
        if offers:
            shopping.add_product(user, max(offers, key=lambda o: o.price))  # dearest, so alternatives exist
    items = budgets.list_items(budgets.get_active_list(budgets.get_active_budget(user.UserId)))
    assert len(items) >= 3
    return user, items


def test_refreshing_alternatives_writes_only_after_every_lookup(list_with_items):
    user, items = list_with_items
    watcher = WatchingProvider(get_retail_provider())
    shopping.refresh_alternatives(items, watcher, user, force=True)
    assert watcher.calls >= len(items)
    assert all(item.CheaperAlternativeJSON["checked_at"] for item in items)  # and the results were saved
    assert not db.session.dirty


def test_the_price_notifications_look_everything_up_before_writing(list_with_items):
    user, _ = list_with_items
    watcher = WatchingProvider(get_retail_provider())
    notifications.evaluate(user, prices=True, provider=watcher)
    assert watcher.calls > 0 and not db.session.dirty and not db.session.new


def test_sqlite_waits_for_other_writers_and_uses_wal(tmp_path, monkeypatch):
    from tests.conftest import config_for_testing

    db_file = tmp_path / "app.db"
    monkeypatch.setattr(config_for_testing(), "SQLALCHEMY_DATABASE_URI", f"sqlite:///{db_file}")
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        assert db.session.execute(text("PRAGMA busy_timeout")).scalar() == 30000
        assert db.session.execute(text("PRAGMA journal_mode")).scalar() == "wal"

        # Another connection holds the write lock for a moment; our write waits for it instead of failing.
        other = sqlite3.connect(db_file, check_same_thread=False)
        other.execute("BEGIN IMMEDIATE")
        threading.Thread(target=lambda: (time.sleep(1.0), other.commit(), other.close())).start()
        db.session.execute(
            text(
                'INSERT INTO stores ("StoreId", "Slug", "Name", "Brand", "Address", "Latitude", '
                "\"Longitude\", \"LocationSource\", \"StoreType\") VALUES ('S1','s1','S','B','A',-29.8,31.0,'approximate','grocery')"
            )
        )
        started = time.monotonic()
        db.session.commit()  # waits for the other writer instead of failing with "database is locked"
        assert time.monotonic() - started < 25
        db.session.remove()
        db.drop_all()
