"""factory_boy factories for the tests.

    UserFactory(password="Str0ng!Pass")           a student who can sign in with e-mail + password
    UserFactory(admin=True)                        an admin
    StoreFactory(brand="Checkers")                 a Durban branch
    BudgetFactory(user=u)                          R800 Grocery + R300 Toiletries (through the real budget service)
    BudgetFactory(user=u, combined=1500)           one Combined Budget of R1 500
    ListItemFactory(budget=b, price="30.00")       an item on the budget's active list, totals recalculated
    ProductFactory(price=Decimal("23.99"))         a retail_api.Product (what a provider returns)

The database factories commit, so they need an app context (the ``app`` fixture provides one).
"""

from decimal import Decimal

import factory
from factory.alchemy import SQLAlchemyModelFactory

from app.extensions import db
from app.models import ListItem, Store, User
from app.services import budgets
from app.services.retail_api import Product

DURBAN = (-29.8587, 31.0218)


class _Base(SQLAlchemyModelFactory):
    class Meta:
        abstract = True
        sqlalchemy_session_factory = (
            lambda: db.session
        )  # noqa: E731 - resolved when a factory runs, inside the app context
        sqlalchemy_session_persistence = "commit"


class UserFactory(_Base):
    class Meta:
        model = User

    class Params:
        admin = False  # UserFactory(admin=True)

    FirstName = factory.Faker("first_name")
    LastName = factory.Faker("last_name")
    Email = factory.Sequence(lambda n: f"student{n}@example.com")
    MicrosoftId = factory.Sequence(lambda n: f"ms-{n}")
    CellphoneNumber = factory.Sequence(lambda n: f"08{n % 100000000:08d}")
    Latitude, Longitude = DURBAN
    IsAdmin = factory.LazyAttribute(lambda o: bool(o.admin))

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        """``UserFactory(password="Str0ng!Pass")``: hashed the way registration does it."""
        if create and extracted:
            obj.set_password(extracted)
            db.session.commit()


class StoreFactory(_Base):
    class Meta:
        model = Store

    Slug = factory.Sequence(lambda n: f"store-{n}")
    Name = factory.LazyAttribute(lambda o: f"{o.Brand} {o.Suburb}")
    Brand = "Checkers"
    Suburb = factory.Iterator(["Gateway", "Musgrave", "Pinetown", "Berea", "Umhlanga"])
    Address = factory.LazyAttribute(lambda o: f"1 Test Road, {o.Suburb}, Durban")
    Latitude = factory.Faker("pyfloat", min_value=-29.90, max_value=-29.80, right_digits=4)
    Longitude = factory.Faker("pyfloat", min_value=30.95, max_value=31.05, right_digits=4)
    LocationSource = "approximate"


class BudgetFactory(factory.Factory):
    """Builds a budget with the real service, so sub-budgets and the active list exist exactly as in the app."""

    class Meta:
        model = dict  # unused: _create returns the Budget

    class Params:
        combined = None  # BudgetFactory(combined=1500) -> one Combined Budget

    user = factory.SubFactory(UserFactory)
    title = factory.Sequence(lambda n: f"Budget {n}")
    entries = factory.LazyAttribute(
        lambda o: (
            [("Combined", Decimal(str(o.combined)))]
            if o.combined
            else [("Grocery", Decimal("800")), ("Toiletries", Decimal("300"))]
        )
    )

    @classmethod
    def _create(cls, model_class, user, title, entries):
        budget = budgets.create_budget(user.UserId, title, list(entries))
        db.session.commit()
        return budget


class ListItemFactory(factory.Factory):
    """An item on the budget's active list; the budget and sub-budget totals are recalculated."""

    class Meta:
        model = ListItem

    budget = factory.SubFactory(BudgetFactory)
    name = factory.Sequence(lambda n: f"Product {n}")
    price = "30.00"
    qty = 1
    category = "Grocery"
    store = "Checkers Gateway"
    barcode = factory.Sequence(lambda n: f"600000000{n:04d}")
    purchased = False
    collected = False  # ticked with the list's "Purchased" button

    @classmethod
    def _create(cls, model_class, budget, name, price, qty, category, store, barcode, purchased, collected):
        shopping_list = budgets.ensure_active_list(budget)
        sub = budgets.sub_budget_for(budget, category)
        item = ListItem(
            UserId=budget.UserId,
            ShoppingListId=shopping_list.ShoppingListId,
            ItemName=name,
            UnitCost=Decimal(str(price)),
            ItemQuantity=qty,
            Category=category,
            StoreName=store,
            BarCode=barcode,
            IsPurchased=purchased,
            IsCollected=collected,
            SubBudgetId=sub.SubBudgetId if sub else None,
        )
        db.session.add(item)
        db.session.commit()
        budgets.recalculate(budget)
        db.session.commit()
        return item


class ProductFactory(factory.Factory):
    """What a retail provider returns for one product at one store."""

    class Meta:
        model = Product

    name = factory.Sequence(lambda n: f"Test Product {n}")
    barcode = factory.Sequence(lambda n: f"700000000{n:04d}")
    price = Decimal("25.99")
    image_url = None
    store_name = "Checkers Gateway"
    store_address = "Gateway Theatre of Shopping, Umhlanga"
    store_lat = -29.7263
    store_lng = 31.0664
    category = "Grocery"
    in_stock = True
    brand = "Testbrand"
    retailer = "Checkers"
    store_id = "checkers-gateway"
