"""Steps 9 and 10: the budget service and the /budget and /budget/new views."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Budget, ListItem, ShoppingList
from app.services import budgets
from app.utils.dates import default_budget_title

D = Decimal


@pytest.fixture
def user(make_user):
    return make_user()


def add_item(
    user,
    shopping_list,
    budget,
    name="Milk",
    price="30.00",
    qty=1,
    category="Grocery",
    purchased=False,
    store="Checkers Gateway",
):
    sub = budgets.sub_budget_for(budget, category)
    item = ListItem(
        UserId=user.UserId,
        ShoppingListId=shopping_list.ShoppingListId,
        ItemName=name,
        UnitCost=D(price),
        ItemQuantity=qty,
        Category=category,
        StoreName=store,
        SubBudgetId=sub.SubBudgetId if sub else None,
        IsPurchased=purchased,
    )
    db.session.add(item)
    db.session.commit()
    budgets.recalculate(budget)
    db.session.commit()
    return item


def new_budget(user, entries=(("Grocery", "800"), ("Toiletries", "300")), title="Sep Budget"):
    return budgets.create_budget(user.UserId, title, [(c, D(a)) for c, a in entries])


# ---------------------------------------------------------------------------------------------- form parsing
def form(**fields):
    return MultiDict(fields)


def test_valid_individual_form(app):
    parsed = budgets.parse_budget_form(
        form(
            title="  Sep   Budget ",
            budget_type="individual",
            amount_Grocery="R700",
            amount_Toiletries="300",
            amount_Clothes="1 400,50",
        )
    )
    assert parsed.ok and parsed.title == "Sep Budget"
    assert parsed.entries == [("Grocery", D("700.00")), ("Toiletries", D("300.00")), ("Clothes", D("1400.50"))]
    assert parsed.total == D("2400.50")


def test_valid_combined_form(app):
    parsed = budgets.parse_budget_form(form(title="X", budget_type="combined", amount_Combined="1500"))
    assert parsed.ok and parsed.entries == [("Combined", D("1500.00"))]


def test_combined_and_individual_are_mutually_exclusive_on_the_server(app):
    parsed = budgets.parse_budget_form(
        form(title="X", budget_type="combined", amount_Combined="1500", amount_Grocery="100")
    )
    assert not parsed.ok and "Combined Budget cannot be used together" in parsed.errors["form"]
    parsed = budgets.parse_budget_form(
        form(title="X", budget_type="individual", amount_Combined="1500", amount_Grocery="100")
    )
    assert not parsed.ok and "cannot be combined" in parsed.errors["form"]


@pytest.mark.parametrize(
    "fields,key",
    [
        (dict(budget_type="individual"), "form"),  # no rows
        (dict(budget_type="nonsense", amount_Grocery="5"), "budget_type"),
        (dict(budget_type="individual", amount_Grocery=""), "amounts"),  # blank amount
        (dict(budget_type="individual", amount_Grocery="0"), "amounts"),
        (dict(budget_type="individual", amount_Grocery="-5"), "amounts"),
        (dict(budget_type="individual", amount_Grocery="abc"), "amounts"),
        (dict(budget_type="individual", amount_Grocery="1e3"), "amounts"),
        (dict(budget_type="individual", amount_Grocery="1000000.01"), "amounts"),
        (
            dict(budget_type="individual", amount_Grocery="600000", amount_Clothes="600000"),
            "form",
        ),  # total over the cap
        (dict(budget_type="combined"), "amounts"),  # combined without an amount
    ],
)
def test_invalid_forms_are_rejected(app, fields, key):
    parsed = budgets.parse_budget_form(form(title="X", **fields))
    assert not parsed.ok and key in parsed.errors and parsed.entries == []


def test_title_rules(app):
    assert "title" in budgets.parse_budget_form(form(title="   ", budget_type="individual", amount_Grocery="5")).errors
    long_title = "x" * (budgets.TITLE_MAX + 1)
    assert (
        "title"
        in budgets.parse_budget_form(form(title=long_title, budget_type="individual", amount_Grocery="5")).errors
    )
    fallback = budgets.parse_budget_form(
        form(title="", budget_type="individual", amount_Grocery="5"), defaults_title="06 Sep 2026 Budget"
    )
    assert fallback.ok and fallback.title == "06 Sep 2026 Budget"


def test_unknown_categories_are_ignored(app):
    parsed = budgets.parse_budget_form(form(title="X", budget_type="individual", amount_Grocery="5", amount_Pets="99"))
    assert parsed.ok and parsed.entries == [("Grocery", D("5.00"))]


def test_default_title_format():
    assert default_budget_title(date(2026, 9, 6)) == "06 Sep 2026 Budget"


@pytest.mark.parametrize(
    "total,warning",
    [
        ("1200", None),
        ("1750", None),
        (
            "1900",
            "Your budget is R1 900, which is R150 above the R1 750 you can still budget this month (NSFAS "
            "allowance). Lower it to save the budget.",
        ),
        (
            "1750.50",
            "Your budget is R1 750.50, which is R0.50 above the R1 750 you can still budget this month (NSFAS "
            "allowance). Lower it to save the budget.",
        ),
    ],
)
def test_nsfas_notice_explains_the_limit(app, total, warning):
    notice = budgets.nsfas_notice(D(total))
    assert (notice["warning"] or "").replace("\u00a0", " ") == (warning or "")


# ---------------------------------------------------------------------------------------------- create / remove
def test_create_budget_saves_subbudgets_active_list_and_deactivates_the_previous(app, user):
    first = new_budget(user, title="First")
    first_list = budgets.get_active_list(first)
    add_item(user, first_list, first, price="50.00", qty=2)

    second = new_budget(user, (("Grocery", "700"), ("Clothes", "400")), title="September Budget")
    assert second.IsActive and second.TotalAmount == D("1100.00") and second.UsedAmount == D("0.00")
    assert sorted((s.Category, s.AllocatedAmount) for s in second.sub_budgets) == [
        ("Clothes", D("400.00")),
        ("Grocery", D("700.00")),
    ]
    active_list = budgets.get_active_list(second)
    assert active_list.Title == "September Budget Shopping List" and active_list.IsActive and active_list.TotalCost == 0

    db.session.refresh(first)
    assert not first.IsActive and first.ClosedDate is not None and first.UsedAmount == 0
    assert not db.session.get(ShoppingList, first_list.ShoppingListId).IsActive
    assert (
        db.session.scalars(db.select(ListItem).where(ListItem.ShoppingListId == first_list.ShoppingListId)).all() == []
    )
    assert budgets.get_active_budget(user.UserId).BudgetId == second.BudgetId
    assert db.session.scalar(db.select(db.func.count()).select_from(Budget).where(Budget.IsActive.is_(True))) == 1


def test_creating_a_budget_never_touches_another_students_budget(app, make_user):
    a, b = make_user(), make_user()
    theirs = new_budget(b)
    new_budget(a)
    assert budgets.get_active_budget(b.UserId).BudgetId == theirs.BudgetId


def test_combined_budget_has_one_combined_subbudget(app, user):
    budget = new_budget(user, (("Combined", "1500"),))
    assert [s.Category for s in budget.sub_budgets] == ["Combined"]
    assert budgets.sub_budget_for(budget, "Clothes").Category == "Combined"  # everything counts against it


def test_remove_active_budget_closes_everything_and_deletes_items(app, user):
    budget = new_budget(user)
    shopping_list = budgets.get_active_list(budget)
    add_item(user, shopping_list, budget, price="40.00", qty=3)
    assert budget.UsedAmount == D("120.00")

    removed = budgets.remove_active_budget(user.UserId)
    assert removed.BudgetId == budget.BudgetId
    db.session.refresh(budget)
    db.session.refresh(shopping_list)
    assert (budget.IsActive, shopping_list.IsActive) == (False, False)
    assert budget.ClosedDate is not None and shopping_list.DateClosed is not None
    assert budget.UsedAmount == 0 and shopping_list.TotalCost == 0
    assert all(not s.IsActive and s.UsedAmount == 0 for s in budget.sub_budgets)
    assert budgets.list_items(shopping_list) == []
    assert budgets.get_active_budget(user.UserId) is None
    assert budgets.remove_active_budget(user.UserId) is None  # nothing left to remove


# ---------------------------------------------------------------------------------------------- the numbers
def test_recalculate_positions_and_categories(app, user):
    budget = new_budget(user, (("Grocery", "800"), ("Toiletries", "300")))
    shopping_list = budgets.get_active_list(budget)
    add_item(user, shopping_list, budget, "Milk", "32.99", 2, "Grocery")
    add_item(user, shopping_list, budget, "Soap", "15.00", 3, "Toiletries")
    add_item(user, shopping_list, budget, "Jeans", "400.00", 1, "Clothes")  # no Clothes allocation

    pos = budgets.position(budget)
    assert pos.total == D("1100.00") and pos.used == D("510.98") and pos.in_list == D("510.98")
    assert pos.already_used == 0 and pos.remaining == D("589.02") and not pos.is_over

    rows = {r.category: r for r in budgets.category_rows(budget)}
    assert rows["Grocery"].used == D("65.98") and rows["Grocery"].remaining == D("734.02")
    assert rows["Toiletries"].used == D("45.00")
    other = rows["Other (not budgeted)"]
    assert other.allocated == 0 and other.used == D("400.00") and other.is_other
    assert sum(r.used for r in rows.values()) == budget.UsedAmount  # the rows always add up


def test_over_budget_is_reported_not_blocked(app, user):
    budget = new_budget(user, (("Grocery", "100"),))
    shopping_list = budgets.get_active_list(budget)
    add_item(user, shopping_list, budget, "Big", "180.00", 1)
    pos = budgets.position(budget)
    assert pos.is_over and pos.over_by == D("80.00") and pos.remaining == D("-80.00") and pos.percent_used == 180.0


def test_used_is_split_into_already_used_and_in_list(app, user):
    budget = new_budget(user, (("Grocery", "1750"),))
    shopping_list = budgets.get_active_list(budget)
    add_item(user, shopping_list, budget, "Milk", "280.00", 1)
    budget.UsedAmount = D("1800.00")  # 1 520 was already used before this list
    pos = budgets.position(budget, shopping_list)
    assert (pos.already_used, pos.in_list, pos.remaining, pos.over_by) == (
        D("1520.00"),
        D("280.00"),
        D("-50.00"),
        D("50.00"),
    )


def _closed_budget(user, created, used, total="1750", active=False):
    budget = Budget(
        UserId=user.UserId,
        Title="B",
        TotalAmount=D(total),
        UsedAmount=D(used),
        IsActive=active,
        DateCreated=created,
        ClosedDate=None if active else created,
    )
    db.session.add(budget)
    db.session.commit()
    return budget


def test_monthly_performance_and_reference_delta(app, user):
    today = date(2026, 9, 20)
    _closed_budget(user, datetime(2026, 9, 3, 8, tzinfo=timezone.utc), "1050")
    _closed_budget(user, datetime(2026, 8, 5, 8, tzinfo=timezone.utc), "1830")
    _closed_budget(user, datetime(2026, 8, 20, 8, tzinfo=timezone.utc), "0")  # abandoned: ignored
    this_month, last_month = budgets.monthly_performance(user.UserId, today)
    assert (this_month.label, this_month.used, this_month.allocated, this_month.percent) == (
        "September",
        D("1050.00"),
        D("1750.00"),
        60.0,
    )
    assert this_month.vs_reference == D("700.00")  # +R700 left of R1 750
    assert (last_month.label, last_month.used, last_month.vs_reference) == ("August", D("1830.00"), D("-80.00"))
    assert last_month.allocated == D("1750.00")  # the abandoned budget is not counted


def test_month_boundaries_use_south_african_time(app, user):
    # 23:30 UTC on 31 August is 01:30 on 1 September in South Africa.
    _closed_budget(user, datetime(2026, 8, 31, 23, 30, tzinfo=timezone.utc), "100")
    this_month, last_month = budgets.monthly_performance(user.UserId, date(2026, 9, 20))
    assert this_month.used == D("100.00") and last_month.used == 0


def test_month_without_a_budget(app, user):
    this_month, last_month = budgets.monthly_performance(user.UserId, date(2026, 9, 20))
    assert not this_month.has_budget and this_month.percent == 0 and this_month.vs_reference == D("1750.00")


def test_six_month_series(app, user):
    _closed_budget(user, datetime(2026, 9, 3, tzinfo=timezone.utc), "1050", total="1750")
    _closed_budget(user, datetime(2026, 4, 3, tzinfo=timezone.utc), "500", total="1000")
    _closed_budget(user, datetime(2026, 3, 3, tzinfo=timezone.utc), "500", total="1000")  # outside the six months
    series = budgets.six_month_series(user.UserId, date(2026, 9, 20))
    assert series["labels"] == ["Apr", "May", "Jun", "Jul", "Aug", "Sep"]
    assert series["allocated"] == [1000.0, 0.0, 0.0, 0.0, 0.0, 1750.0] and series["used"] == [500.0, 0, 0, 0, 0, 1050.0]
    assert series["has_data"] and series["long_labels"][0] == "April 2026"
    assert not budgets.six_month_series("nobody", date(2026, 9, 20))["has_data"]


def test_last_closed_budget_prefers_ones_that_were_shopped(app, user):
    shopped = _closed_budget(user, datetime(2026, 8, 1, tzinfo=timezone.utc), "300")
    _closed_budget(user, datetime(2026, 9, 1, tzinfo=timezone.utc), "0")  # newer but abandoned
    assert budgets.last_closed_budget(user.UserId).BudgetId == shopped.BudgetId


# ---------------------------------------------------------------------------------------------- complete purchase
def test_complete_purchase_records_everything_in_one_go(app, user):
    budget = new_budget(user)
    shopping_list = budgets.get_active_list(budget)
    item = add_item(user, shopping_list, budget, price="60.00", qty=2)
    budgets.complete_purchase(user.UserId)
    for row in (budget, shopping_list, item):
        db.session.refresh(row)
    assert item.IsPurchased and item.PurchasedDate is not None
    assert not shopping_list.IsActive and shopping_list.DateClosed is not None
    assert not budget.IsActive and budget.ClosedDate is not None and budget.UsedAmount == D("120.00")
    assert all(not s.IsActive for s in budget.sub_budgets)
    assert budgets.get_active_budget(user.UserId) is None


def test_complete_purchase_refuses_an_empty_or_over_budget_list(app, user):
    with pytest.raises(budgets.BudgetError, match="active budget"):
        budgets.complete_purchase(user.UserId)
    budget = new_budget(user, (("Grocery", "100"),))
    with pytest.raises(budgets.BudgetError, match="empty"):
        budgets.complete_purchase(user.UserId)
    add_item(user, budgets.get_active_list(budget), budget, price="150.00")
    with pytest.raises(budgets.BudgetError, match="over budget"):
        budgets.complete_purchase(user.UserId)
    assert budgets.get_active_budget(user.UserId) is not None  # nothing was closed


# ---------------------------------------------------------------------------------------------- views
def valid_post(**extra):
    data = {
        "title": "September Budget",
        "budget_type": "individual",
        "amount_Grocery": "700",
        "amount_Toiletries": "300",
        "amount_Clothes": "400",
    }
    data.update(extra)
    return data


def test_budget_pages_need_a_login(client):
    for path in ("/budget", "/budget/new"):
        assert client.get(path).status_code == 302
    assert client.post("/budget/new", data=valid_post()).status_code == 302
    assert client.post("/budget/remove").status_code == 302


def test_new_budget_page_defaults(client, user, login):
    login(user)
    html = client.get("/budget/new").get_data(as_text=True)
    assert default_budget_title() in html
    for category in ("Grocery", "Toiletries", "Clothes"):
        assert f'name="amount_{category}"' in html
    assert "NSFAS Monthly Allowance" in html and "R1 750" in html
    assert "already have an active budget" not in html


def test_saving_a_budget_redirects_and_makes_it_active(client, user, login):
    login(user)
    response = client.post("/budget/new", data=valid_post())
    assert response.status_code == 302 and response.headers["Location"].endswith("/budget")
    budget = budgets.get_active_budget(user.UserId)
    assert budget.Title == "September Budget" and budget.TotalAmount == D("1400.00")
    assert budgets.get_active_list(budget).Title == "September Budget Shopping List"


def test_a_budget_above_the_nsfas_allowance_is_blocked(client, user, login):
    login(user)
    response = client.post("/budget/new", data=valid_post(amount_Grocery="1500"))  # R2 200 in all
    assert response.status_code == 400
    assert budgets.get_active_budget(user.UserId) is None  # blocked
    text = response.get_data(as_text=True).replace("\u00a0", " ")
    assert "Your budget of R2 200 is too high" in text and "at most R1 750 a month" in text


def test_exactly_the_allowance_is_accepted(client, user, login):
    login(user)
    response = client.post("/budget/new", data=valid_post(amount_Grocery="1050"))  # R1 750
    assert response.status_code == 302 and budgets.get_active_budget(user.UserId).TotalAmount == D("1750.00")


def test_money_spent_earlier_this_month_counts_against_the_allowance(client, user, login):
    first = budgets.create_budget(user.UserId, "Early September", [("Grocery", D("1000"))])
    first.UsedAmount = D("800.00")  # shopped R800 of it
    first.deactivate()
    db.session.commit()
    room = budgets.allowance_room(user.UserId)
    assert (room.spent, room.room) == (D("800.00"), D("950.00"))

    login(user)
    blocked = client.post("/budget/new", data=valid_post(amount_Grocery="300"))  # R1 000 > R950 left
    text = blocked.get_data(as_text=True).replace("\u00a0", " ")
    assert blocked.status_code == 400 and "You already spent R800 this month" in text and "at most R950" in text
    assert client.post("/budget/new", data=valid_post(amount_Grocery="250")).status_code == 302  # R950


def test_an_abandoned_budget_does_not_use_up_the_allowance(app, user):
    budgets.create_budget(user.UserId, "Oops", [("Grocery", D("1750"))])
    budgets.remove_active_budget(user.UserId)
    assert budgets.allowance_room(user.UserId).room == D("1750.00")


def test_budget_outcome_shows_used_and_saved(app):
    outcome = budgets.BudgetOutcome(D("1000.00"), D("820.00"))
    assert outcome.saved == D("180.00")
    assert outcome.message().replace("\u00a0", " ") == (
        "You budgeted R1 000 and used R820. You saved R180 from your budget."
    )
    assert "whole budget" in budgets.BudgetOutcome(D("500"), D("500")).message()


def test_invalid_submission_keeps_what_was_typed(client, user, login):
    login(user)
    response = client.post("/budget/new", data=valid_post(amount_Grocery="abc", title="My title"))
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert 'value="abc"' in html and 'value="My title"' in html and "Enter an amount" in html
    assert budgets.get_active_budget(user.UserId) is None


def test_server_refuses_combined_plus_categories(client, user, login):
    login(user)
    response = client.post("/budget/new", data=valid_post(budget_type="combined", amount_Combined="1500"))
    assert response.status_code == 400 and budgets.get_active_budget(user.UserId) is None
    ok = client.post("/budget/new", data={"title": "C", "budget_type": "combined", "amount_Combined": "1500"})
    assert ok.status_code == 302 and [s.Category for s in budgets.get_active_budget(user.UserId).sub_budgets] == [
        "Combined"
    ]


def test_new_budget_page_warns_when_it_will_replace_an_active_one(client, user, login):
    budget = new_budget(user, title="Old one")
    add_item(user, budgets.get_active_list(budget), budget)
    login(user)
    html = client.get("/budget/new").get_data(as_text=True)
    assert "Old one" in html and "removes its shopping list and 1 item" in html


def test_budget_page_without_a_budget(client, user, login):
    login(user)
    html = client.get("/budget").get_data(as_text=True)
    assert "Create New Budget" in html and "Remove Current Budget" not in html and "Proceed to Shopping" not in html


def test_budget_page_with_an_active_budget(client, user, login):
    budget = new_budget(user, (("Grocery", "800"), ("Toiletries", "300")), title="September Budget")
    add_item(user, budgets.get_active_list(budget), budget, price="100.00", qty=2)
    login(user)
    html = client.get("/budget").get_data(as_text=True).replace(" ", " ")
    assert "September Budget" in html and "Remove Current Budget" in html and "Proceed to Shopping" in html
    assert "Confirm Remove" in html and "your active shopping list" in html and "1 item" in html
    assert "R1 100" in html and "R200" in html and "18%" in html
    assert "Grocery" in html and "Toiletries" in html and "This Month" in html and "Last Month" in html
    assert 'href="/budget/new"' not in html.split("Remove Current Budget")[0]  # Create is hidden while one is active
    assert client.get("/budget/").status_code == 200  # trailing slash still works


def test_budget_page_shows_the_last_closed_budget_when_none_is_active(client, user, login):
    budget = new_budget(user, title="August Budget")
    add_item(user, budgets.get_active_list(budget), budget, price="100.00")
    budgets.complete_purchase(user.UserId)
    login(user)
    html = client.get("/budget").get_data(as_text=True)
    assert "August Budget" in html and "Closed" in html and "Create New Budget" in html
    assert "Proceed to Shopping" not in html and "Remove Current Budget" not in html


def test_remove_needs_a_post_and_removes_the_list_too(client, user, login):
    budget = new_budget(user)
    shopping_list = budgets.get_active_list(budget)
    add_item(user, shopping_list, budget)
    login(user)
    assert client.get("/budget/remove").status_code == 405
    response = client.post("/budget/remove", follow_redirects=True)
    assert "were removed" in response.get_data(as_text=True)
    assert budgets.get_active_budget(user.UserId) is None
    assert db.session.scalars(db.select(ListItem)).all() == []
    assert "do not have an active budget" in client.post("/budget/remove", follow_redirects=True).get_data(as_text=True)


# ---------------------------------------------------------------------------------------- factory scenarios (Step 20)
# These read as the rules a student sees: the NSFAS figure warns, the two budget styles never mix, and an over-budget
# list still takes items but cannot go to the summary.
from tests.factories import BudgetFactory, ListItemFactory, UserFactory  # noqa: E402


def test_scenario_nsfas_amount_above_1750_is_refused_by_the_form(app):
    user = UserFactory()
    form = {"title": "Too much", "budget_type": "individual", "amount_Grocery": "1200", "amount_Toiletries": "650"}
    parsed = budgets.parse_budget_form(form, room=budgets.allowance_room(user.UserId))  # R1 850 in all
    assert not parsed.ok and "at most R1" in parsed.errors["form"]


def test_scenario_exactly_1750_has_no_warning(app):
    assert budgets.nsfas_notice(D("1750"))["warning"] is None
    assert budgets.nsfas_notice(D("1750.01"))["warning"]


def test_scenario_combined_and_individual_categories_never_mix(app):
    user = UserFactory()
    with pytest.raises(budgets.BudgetError):
        budgets.create_budget(user.UserId, "Mixed", [("Combined", D("1000")), ("Grocery", D("500"))])
    db.session.rollback()
    assert budgets.get_active_budget(user.UserId) is None  # nothing half-saved

    parsed = budgets.parse_budget_form(
        form(title="Mixed", budget_type="combined", amount_Combined="1000", amount_Grocery="500")
    )
    assert not parsed.ok


def test_scenario_a_combined_budget_is_one_pot_for_every_category(app):
    budget = BudgetFactory(combined=1500)
    ListItemFactory(budget=budget, category="Grocery", price="100.00")
    ListItemFactory(budget=budget, category="Clothes", price="250.00")
    position = budgets.position(budget)
    assert position.in_list == D("350") and position.remaining == D("1150") and not position.is_over


def test_scenario_over_budget_list_still_accepts_items_but_cannot_proceed(client, login):
    user = UserFactory()
    login(user)
    budget = BudgetFactory(user=user, entries=[("Grocery", D("100"))])
    ListItemFactory(budget=budget, price="60.00")
    page = client.get("/list").get_data(as_text=True)
    assert 'href="/list/summary"' in page  # R60 of R100: can proceed

    ListItemFactory(budget=budget, price="60.00")  # R120 of R100: adding was not blocked
    assert budgets.position(budget).is_over
    page = client.get("/list").get_data(as_text=True)
    assert 'href="/list/summary"' not in page
    assert "disabled" in page.split("Proceed to Summary")[0][-200:]
    assert client.get("/list/summary").headers["Location"].endswith("/list")
