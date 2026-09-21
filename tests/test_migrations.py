"""The Alembic history must build the same schema as the models, and must not destroy data on the way."""

import sqlite3
from pathlib import Path

import pytest
from flask_migrate import check, downgrade, upgrade

from app import create_app
from app.config import TestConfig

MIGRATIONS = str(Path(__file__).resolve().parent.parent / "migrations")
INITIAL = "5594a5dcdd08"
BEFORE_STEPS_8_14 = "d6526d03a521"
STEPS_8_14 = "b69bda71d54a"


@pytest.fixture
def migration_app(tmp_path, monkeypatch):
    db_file = tmp_path / "migrate.db"
    monkeypatch.setattr(TestConfig, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{db_file}")
    app = create_app("testing")
    app.db_file = db_file
    return app


def _connect(app):
    connection = sqlite3.connect(app.db_file)
    connection.row_factory = sqlite3.Row
    return connection


def test_upgrade_to_head_matches_the_models(migration_app):
    with migration_app.app_context():
        upgrade(directory=MIGRATIONS)
        check(directory=MIGRATIONS)  # raises if the models differ from the migrated schema
    tables = {row[0] for row in _connect(migration_app).execute("select name from sqlite_master where type='table'")}
    assert {"users", "budgets", "sub_budgets", "shopping_lists", "list_items", "preferences", "stores"} <= tables


def test_upgrade_keeps_existing_rows_and_cascades(migration_app):
    """SQLite rebuilds `users` for the new columns. That must not cascade-delete the student's budgets."""
    with migration_app.app_context():
        upgrade(directory=MIGRATIONS, revision=INITIAL)
    connection = _connect(migration_app)
    connection.execute(
        "insert into users (UserId, FirstName, LastName, Email, MicrosoftId) values ('USR-1','A','B','a@b.co','ms1')"
    )
    connection.execute(
        "insert into budgets (BudgetId, UserId, Title, TotalAmount, UsedAmount, IsActive) values ('BGT-1','USR-1','T',100,0,1)"
    )
    connection.execute(
        "insert into preferences (PreferenceId, UserId, PreferenceType, PreferenceValue) values ('PRF-1','USR-1','Brand','Albany')"
    )
    connection.commit()
    connection.close()

    with migration_app.app_context():
        upgrade(directory=MIGRATIONS)

    connection = _connect(migration_app)
    assert connection.execute("select count(*) from users").fetchone()[0] == 1
    assert connection.execute("select count(*) from budgets").fetchone()[0] == 1
    assert connection.execute("select count(*) from preferences").fetchone()[0] == 1
    assert connection.execute("select version_num from alembic_version").fetchone()[0]  # the upgrade was committed


def test_constraints_survive_the_table_rebuild(migration_app):
    with migration_app.app_context():
        upgrade(directory=MIGRATIONS)
    connection = _connect(migration_app)
    unique_columns = set()
    for index in connection.execute("pragma index_list('users')"):
        if index["unique"]:
            unique_columns.update(col["name"] for col in connection.execute(f"pragma index_info('{index['name']}')"))
    assert {"Email", "MicrosoftId", "SAIdHash", "UserId"} <= unique_columns
    for table in ("budgets", "sub_budgets", "shopping_lists", "list_items", "preferences"):
        actions = {row["on_delete"] for row in connection.execute(f"pragma foreign_key_list('{table}')")}
        assert actions == {"CASCADE"}, table
    assert not connection.execute("pragma foreign_key_check").fetchall()


def test_microsoft_id_is_nullable_after_upgrade(migration_app):
    with migration_app.app_context():
        upgrade(directory=MIGRATIONS)
    columns = {row["name"]: row for row in _connect(migration_app).execute("pragma table_info('users')")}
    assert columns["MicrosoftId"]["notnull"] == 0
    assert {
        "PasswordHash",
        "SAIdHash",
        "SAIdLast4",
        "DateOfBirth",
        "Gender",
        "Race",
        "TermsAcceptedOn",
        "Latitude",
        "Longitude",
    } <= set(columns)


def test_full_downgrade_and_upgrade_round_trip(migration_app):
    with migration_app.app_context():
        upgrade(directory=MIGRATIONS)
        downgrade(directory=MIGRATIONS, revision="base")
        upgrade(directory=MIGRATIONS)
        check(directory=MIGRATIONS)


def test_steps_8_to_14_migration_keeps_existing_list_items(migration_app):
    """A student who already has a list keeps it. Old rows are "not purchased" and have no category or store position."""
    with migration_app.app_context():
        upgrade(directory=MIGRATIONS, revision=BEFORE_STEPS_8_14)
    connection = _connect(migration_app)
    connection.execute(
        "insert into users (UserId, FirstName, LastName, Email, MicrosoftId) values ('USR-1','A','B','a@b.co','ms1')"
    )
    connection.execute(
        "insert into budgets (BudgetId, UserId, Title, TotalAmount, UsedAmount, IsActive) values ('BGT-1','USR-1','T',100,20,1)"
    )
    connection.execute(
        "insert into shopping_lists (ShoppingListId, UserId, BudgetId, Title, TotalCost, IsActive) values ('LST-1','USR-1','BGT-1','L',20,1)"
    )
    connection.execute(
        "insert into list_items (ListItemId, UserId, ShoppingListId, ItemName, UnitCost, ItemQuantity, StoreName) "
        "values ('ITM-1','USR-1','LST-1','Milk 1L',10.00,2,'Checkers')"
    )
    connection.commit()
    connection.close()

    with migration_app.app_context():
        upgrade(directory=MIGRATIONS)
        check(directory=MIGRATIONS)

    connection = _connect(migration_app)
    row = connection.execute("select * from list_items where ListItemId='ITM-1'").fetchone()
    assert (row["ItemName"], row["UnitCost"], row["ItemQuantity"], row["StoreName"]) == ("Milk 1L", 10, 2, "Checkers")
    assert row["IsPurchased"] == 0 and row["PurchasedDate"] is None
    assert row["Category"] is None and row["StoreLatitude"] is None and row["StoreLongitude"] is None
    assert connection.execute("select count(*) from shopping_lists").fetchone()[0] == 1
    assert connection.execute("select count(*) from budgets").fetchone()[0] == 1
    assert not connection.execute("pragma foreign_key_check").fetchall()
    connection.execute(
        "insert into list_items (ListItemId, UserId, ShoppingListId, ItemName, UnitCost, ItemQuantity) "
        "values ('ITM-2','USR-1','LST-1','Rice',50,1)"
    )  # the column default applies to new rows too
    assert connection.execute("select IsPurchased from list_items where ListItemId='ITM-2'").fetchone()[0] == 0


def test_steps_8_to_14_downgrade_keeps_the_rows(migration_app):
    with migration_app.app_context():
        upgrade(directory=MIGRATIONS)
    connection = _connect(migration_app)
    connection.execute(
        "insert into users (UserId, FirstName, LastName, Email, MicrosoftId) values ('USR-1','A','B','a@b.co','ms1')"
    )
    connection.execute(
        "insert into budgets (BudgetId, UserId, Title, TotalAmount, UsedAmount, IsActive) values ('BGT-1','USR-1','T',100,20,1)"
    )
    connection.execute(
        "insert into shopping_lists (ShoppingListId, UserId, BudgetId, Title, TotalCost, IsActive) values ('LST-1','USR-1','BGT-1','L',20,1)"
    )
    connection.execute(
        "insert into list_items (ListItemId, UserId, ShoppingListId, ItemName, UnitCost, ItemQuantity, IsPurchased) "
        "values ('ITM-1','USR-1','LST-1','Milk 1L',10.00,2,1)"
    )
    connection.commit()
    connection.close()
    with migration_app.app_context():
        downgrade(directory=MIGRATIONS, revision=BEFORE_STEPS_8_14)
    connection = _connect(migration_app)
    assert connection.execute("select ItemName from list_items").fetchone()[0] == "Milk 1L"
    assert "IsPurchased" not in {row["name"] for row in connection.execute("pragma table_info('list_items')")}
