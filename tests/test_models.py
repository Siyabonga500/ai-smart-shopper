from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Budget, ListItem, Preference, ShoppingList, SubBudget, User, load_user

ALL_MODELS = (User, Budget, SubBudget, ShoppingList, ListItem, Preference)


def _count(model):
    return db.session.scalar(select(func.count()).select_from(model))


def _budget_with_list(user, title="September Budget", total="1750.00"):
    budget = Budget(UserId=user.UserId, Title=title, TotalAmount=Decimal(total))
    db.session.add(budget)
    db.session.flush()
    grocery = SubBudget(
        UserId=user.UserId, BudgetId=budget.BudgetId, Category="Grocery", AllocatedAmount=Decimal("800.00")
    )
    shopping_list = ShoppingList(UserId=user.UserId, BudgetId=budget.BudgetId, Title="September List")
    db.session.add_all([grocery, shopping_list])
    db.session.flush()
    return budget, grocery, shopping_list


def _add_item(user, shopping_list, sub_budget=None, name="Milk 2L", **extra):
    item = ListItem(
        UserId=user.UserId,
        ShoppingListId=shopping_list.ShoppingListId,
        SubBudgetId=sub_budget.SubBudgetId if sub_budget else None,
        ItemName=name,
        **extra,
    )
    db.session.add(item)
    db.session.flush()
    return item


def _populate(user):
    """One budget, one sub-budget, one list, one item and one preference for ``user``."""
    budget, grocery, shopping_list = _budget_with_list(user)
    item = _add_item(user, shopping_list, grocery)
    pref = Preference(UserId=user.UserId, PreferenceType="Store", PreferenceValue="Checkers")
    db.session.add(pref)
    db.session.commit()
    return budget, grocery, shopping_list, item, pref


# --------------------------------------------------------------------------- schema


def test_the_six_pdf_tables_exist_plus_the_tables_added_by_later_steps(app):
    assert set(db.metadata.tables) == {
        "users",
        "budgets",
        "sub_budgets",
        "shopping_lists",
        "list_items",
        "preferences",
        "stores",  # Step 7: shared reference data, not one of the PDF's six models
        "notifications",  # Step 17
        "category_mappings",
        "integration_settings",
        "audit_logs",  # Step 16: admin portal
        "catalogue_products",  # products admins add by hand
        "contact_messages",  # About us > Contact us
    }


def test_generated_ids_are_prefixed_uuids_that_fit_string_30(make_user):
    user = make_user()
    budget, grocery, shopping_list, item, pref = _populate(user)

    expected = {
        user.UserId: "USR-",
        budget.BudgetId: "BGT-",
        grocery.SubBudgetId: "SBG-",
        shopping_list.ShoppingListId: "LST-",
        item.ListItemId: "ITM-",
        pref.PreferenceId: "PRF-",
    }
    for value, prefix in expected.items():
        assert value.startswith(prefix)
        assert len(value) <= 30
    assert len(set(expected)) == 6


def test_relationships_and_backrefs(make_user):
    user = make_user()
    budget, grocery, shopping_list = _budget_with_list(user)
    item = _add_item(
        user,
        shopping_list,
        grocery,
        name="Clover Full Cream Milk 2L",
        UnitCost=Decimal("29.99"),
        ItemQuantity=2,
        BarCode="6001234567890",
    )
    db.session.commit()

    assert user.budgets == [budget]
    assert budget.user is user
    assert budget.sub_budgets == [grocery]
    assert budget.shopping_lists == [shopping_list]
    assert shopping_list.list_items == [item]
    assert grocery.list_items == [item]
    assert item.shopping_list is shopping_list
    assert item.sub_budget is grocery
    assert item.user is user


def test_defaults(make_user):
    user = make_user()
    budget, _, shopping_list = _budget_with_list(user)
    db.session.commit()
    assert budget.IsActive is True
    assert budget.UsedAmount == Decimal("0.00")
    assert budget.ClosedDate is None
    assert shopping_list.IsActive is True
    assert shopping_list.TotalCost == Decimal("0.00")
    assert shopping_list.DateClosed is None
    assert user.CreatedOn is not None


def test_list_item_sub_budget_is_optional(make_user):
    user = make_user()
    _, _, shopping_list = _budget_with_list(user)
    item = _add_item(user, shopping_list, sub_budget=None)
    db.session.commit()
    assert item.SubBudgetId is None and item.sub_budget is None


def test_total_item_cost_property(make_user):
    user = make_user()
    _, _, shopping_list = _budget_with_list(user)
    item = _add_item(user, shopping_list, name="Sunlight Soap 175g", UnitCost=Decimal("8.99"), ItemQuantity=3)
    db.session.commit()
    assert item.TotalItemCost == Decimal("26.97") and isinstance(item.TotalItemCost, Decimal)


def test_cheaper_alternative_json_round_trip(make_user):
    user = make_user()
    _, _, shopping_list = _budget_with_list(user)
    alternative = {"name": "Parmalat Full Cream Milk 2L", "store": "Checkers", "price": 24.99}
    item = _add_item(user, shopping_list, name="Clover Full Cream Milk 2L", CheaperAlternativeJSON=alternative)
    db.session.commit()
    db.session.expire_all()
    assert db.session.get(ListItem, item.ListItemId).CheaperAlternativeJSON == alternative


def test_email_and_microsoft_id_are_unique(make_user):
    first = make_user(email="a@example.com")
    db.session.add(User(FirstName="X", LastName="Y", Email=first.Email, MicrosoftId="different"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    db.session.add(User(FirstName="X", LastName="Y", Email="other@example.com", MicrosoftId=first.MicrosoftId))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_microsoft_id_is_optional_but_still_unique(app):
    """Step 3: password-only accounts have no MicrosoftId. Several NULLs are fine; equal values are not."""
    db.session.add_all(
        [
            User(FirstName="X", LastName="Y", Email="pw1@example.com"),
            User(FirstName="X", LastName="Y", Email="pw2@example.com"),
        ]
    )
    db.session.commit()

    db.session.add_all(
        [
            User(FirstName="A", LastName="B", Email="ms1@example.com", MicrosoftId="same"),
            User(FirstName="A", LastName="B", Email="ms2@example.com", MicrosoftId="same"),
        ]
    )
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_flask_login_integration(make_user):
    user = make_user(first="Thabo", last="Mokoena")
    assert user.get_id().startswith(f"{user.UserId}:")  # the id plus a password fingerprint
    assert load_user(user.get_id()) is user
    assert load_user(user.UserId) is None  # the bare id (no fingerprint) is not a session
    assert load_user(f"{user.UserId}:wrong") is None
    assert load_user("does-not-exist:abc") is None
    assert load_user("") is None
    assert user.FullName == "Thabo Mokoena"


# ------------------------------------------------------------ Budget helper properties


@pytest.mark.parametrize(
    "total, used, remaining, percent",
    [
        ("1750.00", "1050.00", Decimal("700.00"), 60.0),  # Dashboard mock-up: R1 750 / R1 050 / R700 / 60%
        ("1750.00", "0.00", Decimal("1750.00"), 0.0),
        ("1750.00", "1830.00", Decimal("-80.00"), 104.57),  # over budget: negative remaining, over 100%
        ("1750.00", "1750.00", Decimal("0.00"), 100.0),
        ("0.00", "0.00", Decimal("0.00"), 0.0),  # no total: no ZeroDivisionError
        ("0.00", "50.00", Decimal("-50.00"), 0.0),
        ("300.00", "100.00", Decimal("200.00"), 33.33),
    ],
)
def test_budget_remaining_and_percent_used(total, used, remaining, percent):
    budget = Budget(Title="t", TotalAmount=Decimal(total), UsedAmount=Decimal(used))
    assert budget.remaining == remaining
    assert budget.percent_used == pytest.approx(percent)


def test_budget_properties_work_before_defaults_are_applied():
    budget = Budget(Title="unsaved")  # TotalAmount / UsedAmount are still None
    assert budget.remaining == 0
    assert budget.percent_used == 0.0


def test_budget_properties_accept_floats_without_binary_noise():
    budget = Budget(Title="t", TotalAmount=1750.10, UsedAmount=0.10)
    assert budget.remaining == Decimal("1750.00")


def test_budget_properties_after_a_database_round_trip(make_user):
    user = make_user()
    budget, *_ = _budget_with_list(user, total="1750.00")
    budget.UsedAmount = Decimal("1050.00")
    db.session.commit()
    db.session.expire_all()
    fresh = db.session.get(Budget, budget.BudgetId)
    assert fresh.remaining == Decimal("700.00")
    assert fresh.percent_used == 60.0


# ------------------------------------------------------------ activate / deactivate


def test_budget_deactivate_and_activate(make_user):
    user = make_user()
    budget, *_ = _budget_with_list(user)
    db.session.commit()
    assert budget.IsActive is True and budget.ClosedDate is None

    budget.deactivate()
    db.session.commit()
    assert budget.IsActive is False
    assert budget.ClosedDate is not None

    budget.activate()
    db.session.commit()
    assert budget.IsActive is True
    assert budget.ClosedDate is None


def test_shopping_list_uses_date_closed(make_user):
    user = make_user()
    _, _, shopping_list = _budget_with_list(user)
    db.session.commit()

    shopping_list.deactivate()
    db.session.commit()
    assert shopping_list.IsActive is False
    assert shopping_list.DateClosed is not None

    shopping_list.activate()
    db.session.commit()
    assert shopping_list.IsActive is True
    assert shopping_list.DateClosed is None


def test_deactivate_accepts_an_explicit_time_and_keeps_the_first_close_time():
    budget = Budget(Title="t")
    first = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    budget.deactivate(when=first)
    assert budget.ClosedDate == first

    budget.deactivate()  # second call must not move the timestamp
    assert budget.ClosedDate == first

    later = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    budget.deactivate(when=later)  # ...unless a time is passed on purpose
    assert budget.ClosedDate == later


def test_deactivate_stamps_a_current_utc_time():
    before = datetime.now(timezone.utc)
    budget = Budget(Title="t")
    budget.deactivate()
    assert before <= budget.ClosedDate <= datetime.now(timezone.utc)


# ------------------------------------------------------------------- cascade deletes


def test_deleting_a_user_removes_everything_they_own(make_user):
    victim, bystander = make_user(), make_user()
    _populate(victim)
    _populate(bystander)
    assert [_count(m) for m in ALL_MODELS] == [2, 2, 2, 2, 2, 2]

    db.session.delete(victim)
    db.session.commit()

    # Only the bystander's rows survive, one of each kind.
    assert [_count(m) for m in ALL_MODELS] == [1, 1, 1, 1, 1, 1]
    assert db.session.scalar(select(func.count()).select_from(Budget).where(Budget.UserId == victim.UserId)) == 0
    assert db.session.get(User, bystander.UserId) is not None


def test_deleting_a_budget_removes_its_sub_budgets_lists_and_items_but_not_the_user(make_user):
    user = make_user()
    budget, *_ = _populate(user)
    other_budget, other_grocery, other_list = _budget_with_list(user, title="Other")
    _add_item(user, other_list, other_grocery, name="Bread")
    db.session.commit()
    assert _count(ListItem) == 2

    db.session.delete(budget)
    db.session.commit()

    assert (_count(User), _count(Budget), _count(SubBudget), _count(ShoppingList), _count(ListItem)) == (1, 1, 1, 1, 1)
    assert _count(Preference) == 1  # preferences belong to the user, not the budget
    assert db.session.get(Budget, other_budget.BudgetId) is not None


def test_deleting_a_shopping_list_removes_only_its_items(make_user):
    user = make_user()
    budget, grocery, shopping_list, *_ = _populate(user)
    second_list = ShoppingList(UserId=user.UserId, BudgetId=budget.BudgetId, Title="Second")
    db.session.add(second_list)
    db.session.flush()
    _add_item(user, second_list, grocery, name="Eggs")
    db.session.commit()
    assert _count(ListItem) == 2

    db.session.delete(shopping_list)
    db.session.commit()

    assert _count(ListItem) == 1
    assert _count(Budget) == 1 and _count(SubBudget) == 1


def test_sqlite_foreign_keys_are_enforced(app):
    with db.engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_orphan_rows_are_rejected_by_the_database(app):
    db.session.add(Budget(UserId="USR-does-not-exist", Title="orphan"))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_database_level_cascade_covers_bulk_deletes_that_skip_the_orm(make_user):
    """``ON DELETE CASCADE`` on every foreign key, so even a raw DELETE cleans up."""
    user = make_user()
    _populate(user)
    user_id = user.UserId
    db.session.expunge_all()

    db.session.execute(delete(User).where(User.UserId == user_id))
    db.session.commit()

    assert [_count(m) for m in ALL_MODELS] == [0, 0, 0, 0, 0, 0]


def test_every_foreign_key_declares_on_delete_cascade(app):
    for model in ALL_MODELS:
        for fk in inspect(model).local_table.foreign_keys:
            assert fk.ondelete == "CASCADE", f"{model.__tablename__}.{fk.parent.name}"


def test_orm_cascade_works_even_when_the_database_enforces_nothing(make_user):
    """The ORM cascade must not depend on the DB: delete with SQLite FK enforcement switched off."""
    victim = make_user()
    _populate(victim)
    db.session.commit()
    db.session.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        db.session.delete(victim)
        db.session.commit()
        assert [_count(m) for m in ALL_MODELS] == [0, 0, 0, 0, 0, 0]
    finally:
        db.session.execute(text("PRAGMA foreign_keys=ON"))


# ------------------------------------------------------------------------------------------ factory_boy scenarios

from tests.factories import BudgetFactory, ListItemFactory, StoreFactory, UserFactory  # noqa: E402


def test_the_factories_build_valid_rows_that_survive_a_round_trip(app):
    user, store = UserFactory(password="Str0ng!Pass"), StoreFactory()
    budget = BudgetFactory(user=user)
    item = ListItemFactory(budget=budget, price="12.34", qty=3)
    db.session.expire_all()
    assert user.check_password("Str0ng!Pass") and not user.check_password("wrong")
    assert (user.IsAdmin, user.IsActive) == (False, True) and UserFactory(admin=True).IsAdmin is True
    assert store.LocationSource == "approximate" and store.Latitude < 0
    assert item.LineTotal == Decimal("37.02") and budget.UsedAmount == Decimal("37.02")


def test_a_duplicate_store_slug_or_email_is_refused(app):
    StoreFactory(Slug="checkers-gateway")
    with pytest.raises(IntegrityError):
        StoreFactory(Slug="checkers-gateway")
    db.session.rollback()
    UserFactory(Email="same@example.com")
    with pytest.raises(IntegrityError):
        UserFactory(Email="same@example.com")
    db.session.rollback()


def test_money_stays_exact_across_many_small_items(app):
    budget = BudgetFactory(combined=100)
    for _ in range(10):
        ListItemFactory(budget=budget, price="0.10", category="Combined")
    db.session.refresh(budget)
    assert budget.UsedAmount == Decimal("1.00") and budget.remaining == Decimal(
        "99.00"
    )  # 10 x 0.1 is exactly 1.00, not 0.99999...


def test_an_overspent_budget_reports_more_than_100_percent_and_a_negative_remainder(app):
    budget = BudgetFactory(combined=100)
    ListItemFactory(budget=budget, price="103.00", category="Combined")
    db.session.refresh(budget)
    assert budget.percent_used == 103.0 and budget.remaining == Decimal("-3.00")


def test_deactivated_users_are_not_loaded_by_flask_login(app):
    user = UserFactory()
    user.IsActive = False
    db.session.commit()
    assert user.is_active is False
    from app.models.user import load_user

    assert load_user(user.UserId) is None
