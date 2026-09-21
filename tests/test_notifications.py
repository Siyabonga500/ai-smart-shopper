"""Step 17: the notifications table, the rules that fill it, the JSON endpoints and the bell."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import cache, db
from app.models import ListItem, Notification
from app.services import budgets, notifications, shopping
from app.services.retail_api import RetailAPIError, RetailConfigError, get_retail_provider

D = Decimal
MILK = "2000000000275"  # Clover Long Life Full Cream Milk 1L, sold by several retailers in the mock catalogue


# ---------------------------------------------------------------------------------------------------- helpers
@pytest.fixture
def user(make_user):
    return make_user(Latitude=-29.8587, Longitude=31.0218)


@pytest.fixture
def budget(user):
    return budgets.create_budget(user.UserId, "Sep Budget", [("Grocery", D("100"))])


@pytest.fixture
def provider(app):
    return get_retail_provider()


def spend(user, budget, total):
    """Make the active list cost exactly ``total`` rand (one filler item), and save the recalculated budget."""
    shopping_list = budgets.ensure_active_list(budget)
    item = db.session.scalar(
        select(ListItem).where(ListItem.ShoppingListId == shopping_list.ShoppingListId, ListItem.ItemName == "Filler")
    )
    if item is None:
        item = ListItem(
            UserId=user.UserId,
            ShoppingListId=shopping_list.ShoppingListId,
            ItemName="Filler",
            UnitCost=D("0"),
            ItemQuantity=1,
            Category="Grocery",
        )
        db.session.add(item)
    item.UnitCost = D(str(total))
    budgets.recalculate(budget)
    db.session.commit()


def titles(user_id, kind=None):
    query = (
        select(Notification)
        .where(Notification.UserId == user_id)
        .order_by(Notification.CreatedOn, Notification.NotificationId)
    )
    return [n.Title for n in db.session.scalars(query) if kind is None or n.Type == kind]


def cheapest_and_dearest(provider, user):
    center = shopping.student_center(user)
    offers = sorted(
        provider.get_offers_by_barcode(MILK, center["lat"], center["lng"], 15), key=lambda o: (o.price, o.store_name)
    )
    return offers[0], offers[-1]


# ------------------------------------------------------------------------------------------------------ the table
def test_the_same_event_cannot_be_stored_twice_for_a_student(user):
    db.session.add(Notification(UserId=user.UserId, Type="over_budget", Title="a", Message="m", DedupeKey="k"))
    db.session.commit()
    db.session.add(Notification(UserId=user.UserId, Type="over_budget", Title="b", Message="m", DedupeKey="k"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_two_students_may_share_a_key_and_a_null_key_is_never_a_clash(make_user):
    one, two = make_user(), make_user()
    for owner in (one, two, one, one):
        db.session.add(
            Notification(
                UserId=owner.UserId, Type="over_budget", Title="t", Message="m", DedupeKey=None if owner is one else "k"
            )
        )
    db.session.add(Notification(UserId=one.UserId, Type="over_budget", Title="t", Message="m", DedupeKey="k"))
    db.session.commit()
    assert db.session.query(Notification).count() == 5


def test_defaults_and_columns(user):
    row = notifications.notify(user.UserId, "price_drop", "Milk is cheaper", "details", dedupe_key="x", url="/list")
    db.session.commit()
    assert row.NotificationId.startswith("NTF-") and len(row.NotificationId) <= 30
    assert row.IsRead is False and row.CreatedOn is not None
    assert (row.level, row.icon) == ("success", "graph-down-arrow")


def test_unknown_types_are_refused(user):
    with pytest.raises(ValueError):
        notifications.notify(user.UserId, "spam", "t", "m")


def test_notify_returns_none_for_a_repeat(user):
    assert notifications.notify(user.UserId, "over_budget", "t", "m", dedupe_key="k")
    db.session.commit()
    assert notifications.notify(user.UserId, "over_budget", "t", "m", dedupe_key="k") is None
    assert notifications.notify(user.UserId, "over_budget", "t", "m") is not None  # no key: never de-duplicated


def test_losing_a_race_skips_only_that_row(user, monkeypatch):
    """If another request wrote the key between our check and our insert, the database refuses and we carry on."""
    db.session.add(Notification(UserId=user.UserId, Type="over_budget", Title="first", Message="m", DedupeKey="k"))
    db.session.commit()
    monkeypatch.setattr(db.session, "scalar", lambda *a, **k: None)  # the pre-check "sees" nothing
    assert notifications.notify(user.UserId, "over_budget", "second", "m", dedupe_key="k") is None
    monkeypatch.undo()
    db.session.commit()
    assert titles(user.UserId) == ["first"]


def test_deleting_a_student_deletes_their_notifications(user):
    notifications.notify(user.UserId, "over_budget", "t", "m")
    db.session.commit()
    db.session.delete(user)
    db.session.commit()
    assert db.session.query(Notification).count() == 0


def test_release_keeps_the_history_but_frees_the_key(user):
    notifications.notify(user.UserId, "over_budget", "t", "m", dedupe_key="over:1")
    db.session.commit()
    notifications.release(user.UserId, "over:")
    row = db.session.scalars(select(Notification)).one()
    assert row.DedupeKey is None and row.Title == "t"
    assert notifications.notify(user.UserId, "over_budget", "again", "m", dedupe_key="over:1")


# ------------------------------------------------------------------------------------------ budget thresholds
@pytest.mark.parametrize(
    "spent, expected",
    [
        (49, []),
        (50, ["50% of your budget used"]),
        (74.99, ["50% of your budget used"]),
        (75, ["75% of your budget used"]),
        (89, ["75% of your budget used"]),
        (90, ["90% of your budget used"]),
        (99.99, ["90% of your budget used"]),
        (100, ["Budget fully used"]),
    ],
)
def test_each_threshold_is_announced_once_it_is_reached(user, budget, spent, expected):
    spend(user, budget, spent)
    notifications.evaluate(user)
    assert titles(user.UserId, "budget_threshold") == expected


def test_only_the_highest_level_is_announced_when_spending_jumps(user, budget):
    spend(user, budget, 95)
    created = notifications.evaluate(user)
    assert [n.Title for n in created] == ["90% of your budget used"]


def test_a_level_is_announced_once_even_if_evaluated_again(user, budget):
    spend(user, budget, 55)
    notifications.evaluate(user)
    assert notifications.evaluate(user) == []
    assert len(titles(user.UserId)) == 1


def test_levels_climb_one_at_a_time(user, budget):
    for amount in (55, 80, 92, 100):
        spend(user, budget, amount)
        notifications.evaluate(user)
    assert titles(user.UserId, "budget_threshold") == [
        "50% of your budget used",
        "75% of your budget used",
        "90% of your budget used",
        "Budget fully used",
    ]


def test_a_lower_level_is_not_announced_after_a_higher_one(user, budget):
    spend(user, budget, 92)
    notifications.evaluate(user)
    spend(user, budget, 60)  # the student removes items
    assert notifications.evaluate(user) == []
    assert titles(user.UserId) == ["90% of your budget used"]


def test_the_message_says_how_much_is_left(user, budget):
    spend(user, budget, 80)
    message = notifications.evaluate(user)[0].Message
    assert "80%" in message and "R80 of R100" in message and "R20 left" in message


def test_a_new_budget_starts_again(user, budget):
    spend(user, budget, 80)
    notifications.evaluate(user)
    budgets.remove_active_budget(user.UserId)
    fresh = budgets.create_budget(user.UserId, "Oct Budget", [("Grocery", D("100"))])
    spend(user, fresh, 80)
    assert [n.Title for n in notifications.evaluate(user)] == ["75% of your budget used"]


def test_thresholds_can_be_configured(app, user, budget):
    app.config["NOTIFICATION_BUDGET_THRESHOLDS"] = (10, 20)
    spend(user, budget, 15)
    assert [n.Title for n in notifications.evaluate(user)] == ["10% of your budget used"]


def test_no_budget_no_notifications(user):
    assert notifications.evaluate(user) == []


# ------------------------------------------------------------------------------------------------ over budget
def test_over_budget_is_announced_instead_of_a_threshold(user, budget):
    spend(user, budget, 130)
    created = notifications.evaluate(user)
    assert [(n.Type, n.Title) for n in created] == [("over_budget", "Your list is over budget")]
    assert "R30 over Sep Budget" in created[0].Message and created[0].Url == "/list"


def test_over_budget_is_announced_once_until_it_is_fixed(user, budget):
    spend(user, budget, 130)
    notifications.evaluate(user)
    spend(user, budget, 140)
    assert notifications.evaluate(user) == []
    spend(user, budget, 90)  # fixed
    notifications.evaluate(user)
    spend(user, budget, 120)  # over again
    assert [n.Type for n in notifications.evaluate(user)] == ["over_budget"]
    assert len(titles(user.UserId, "over_budget")) == 2


def test_category_budget_exceeded_is_announced_by_category(app, user):
    budget = budgets.create_budget(user.UserId, "Sep Budget", [("Grocery", D("50")), ("Toiletries", D("100"))])
    spend(user, budget, 60)
    item = db.session.scalar(select(ListItem).where(ListItem.ItemName == "Filler"))
    item.SubBudgetId = budgets.sub_budget_for(budget, "Grocery").SubBudgetId
    budgets.recalculate(budget)
    db.session.commit()

    created = notifications.evaluate(user)

    category = next(notification for notification in created if notification.Title == "Grocery budget exceeded")
    assert category.Type == "over_budget"
    assert "R10 over your Grocery budget" in category.Message


def test_category_budget_alert_reappears_after_category_is_fixed(user):
    budget = budgets.create_budget(user.UserId, "Sep Budget", [("Grocery", D("100")), ("Toiletries", D("100"))])
    spend(user, budget, 130)
    item = db.session.scalar(select(ListItem).where(ListItem.ItemName == "Filler"))
    item.SubBudgetId = budgets.sub_budget_for(budget, "Grocery").SubBudgetId
    budgets.recalculate(budget)
    db.session.commit()
    notifications.evaluate(user)

    spend(user, budget, 90)
    assert notifications.evaluate(user) == []

    spend(user, budget, 120)
    created = notifications.evaluate(user)

    assert [notification.Title for notification in created] == ["Grocery budget exceeded"]


def test_exactly_on_budget_is_not_over(user, budget):
    spend(user, budget, 100)
    assert titles(user.UserId, "over_budget") == []
    assert [n.Type for n in notifications.evaluate(user)] == ["budget_threshold"]


# ------------------------------------------------------------------------------------------------ NSFAS reference
def test_monthly_nsfas_reference_is_announced_once_a_month(user):
    big = budgets.create_budget(user.UserId, "Big", [("Grocery", D("2500"))])
    spend(user, big, 1800)
    created = notifications.evaluate(user)
    assert "nsfas_reference" in {n.Type for n in created}
    message = next(n.Message for n in created if n.Type == "nsfas_reference")
    assert "R1\u00a0800" in message and "R50 above the R1\u00a0750" in message and "nothing is blocked" in message
    assert [n.Type for n in notifications.evaluate(user)] == []


def test_spending_at_or_below_the_reference_is_quiet(user):
    big = budgets.create_budget(user.UserId, "Big", [("Grocery", D("2500"))])
    spend(user, big, 1750)
    assert titles(user.UserId, "nsfas_reference") == []
    notifications.evaluate(user)
    assert titles(user.UserId, "nsfas_reference") == []


def test_nsfas_key_is_per_month(user):
    big = budgets.create_budget(user.UserId, "Big", [("Grocery", D("2500"))])
    spend(user, big, 1800)
    notifications.evaluate(user, now=datetime(2026, 9, 20, 10, tzinfo=timezone.utc))
    keys = set(db.session.scalars(select(Notification.DedupeKey).where(Notification.Type == "nsfas_reference")))
    assert len(keys) == 1 and next(iter(keys)).startswith("nsfas:")


# --------------------------------------------------------------------------------------------- price drops
def add_milk(user, offer, quantity=1):
    return shopping.add_product(user, offer, quantity)


def test_a_price_drop_of_ten_percent_is_announced(user, budget, provider):
    cheapest, _ = cheapest_and_dearest(provider, user)
    add_milk(user, cheapest)
    item = db.session.scalars(select(ListItem).where(ListItem.BarCode == MILK)).one()
    item.UnitCost = (cheapest.price / D("0.85")).quantize(
        D("0.01")
    )  # the store now charges 15% less than the list says
    db.session.commit()
    created = notifications.check_prices(user, provider)
    drops = [n for n in created if n.Type == "price_drop"]
    assert len(drops) == 1
    assert cheapest.store_name in drops[0].Message and "% cheaper" in drops[0].Message
    assert drops[0].Title.startswith("Price drop: ")


def test_a_small_drop_is_not_announced(user, budget, provider):
    cheapest, _ = cheapest_and_dearest(provider, user)
    add_milk(user, cheapest)
    item = db.session.scalars(select(ListItem).where(ListItem.BarCode == MILK)).one()
    item.UnitCost = (cheapest.price / D("0.95")).quantize(D("0.01"))  # only about 5% cheaper now
    db.session.commit()
    assert [n for n in notifications.check_prices(user, provider) if n.Type == "price_drop"] == []


def test_a_price_drop_is_announced_once_per_price(user, budget, provider):
    cheapest, _ = cheapest_and_dearest(provider, user)
    add_milk(user, cheapest)
    item = db.session.scalars(select(ListItem).where(ListItem.BarCode == MILK)).one()
    item.UnitCost = (cheapest.price / D("0.8")).quantize(D("0.01"))
    db.session.commit()
    assert len([n for n in notifications.check_prices(user, provider) if n.Type == "price_drop"]) == 1
    db.session.commit()
    assert [n for n in notifications.check_prices(user, provider) if n.Type == "price_drop"] == []


def test_the_price_on_the_list_is_never_changed_by_a_notification(user, budget, provider):
    cheapest, _ = cheapest_and_dearest(provider, user)
    add_milk(user, cheapest)
    item = db.session.scalars(select(ListItem).where(ListItem.BarCode == MILK)).one()
    item.UnitCost = D("99.99")
    db.session.commit()
    notifications.check_prices(user, provider)
    db.session.commit()
    assert db.session.get(ListItem, item.ListItemId).UnitCost == D("99.99")


# ------------------------------------------------------------------------------------- cheaper alternatives
def test_a_cheaper_alternative_for_a_list_item_is_announced(user, budget, provider):
    cheapest, dearest = cheapest_and_dearest(provider, user)
    assert dearest.price > cheapest.price
    add_milk(user, dearest)
    created = [n for n in notifications.check_prices(user, provider) if n.Type == "cheaper_alternative"]
    assert len(created) == 1
    assert cheapest.store_name in created[0].Message and "less than" in created[0].Message
    assert created[0].Url.endswith("alternatives=1")
    db.session.commit()
    assert [n for n in notifications.check_prices(user, provider) if n.Type == "cheaper_alternative"] == []


def test_the_cheapest_offer_has_no_alternative_notice(user, budget, provider):
    cheapest, _ = cheapest_and_dearest(provider, user)
    add_milk(user, cheapest)
    assert [n for n in notifications.check_prices(user, provider) if n.Type == "cheaper_alternative"] == []


def test_alternative_notices_are_capped_per_check(app, user, budget, provider):
    app.config["NOTIFICATION_MAX_NEW_ALTERNATIVES"] = 1
    center = shopping.student_center(user)
    for offer in provider.search_products("milk", center["lat"], center["lng"], 15)[:12]:
        try:
            shopping.add_product(user, offer, 1)
        except shopping.ShoppingError:
            pass
    created = [n for n in notifications.check_prices(user, provider) if n.Type == "cheaper_alternative"]
    assert len(created) <= 1


class _Broken:
    def __init__(self, error):
        self.error = error

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise self.error

        return fail


@pytest.mark.parametrize("error", [RetailAPIError("down"), RetailConfigError("no key")])
def test_a_provider_failure_never_breaks_evaluation(user, budget, provider, error):
    cheapest, _ = cheapest_and_dearest(provider, user)
    add_milk(user, cheapest)
    assert notifications.check_prices(user, _Broken(error)) == []
    spend(user, budget, 80)  # with the milk this is over the R100 budget: the budget rules still speak
    created = notifications.evaluate(user, prices=True, provider=_Broken(error))
    assert created and {n.Type for n in created} <= {"budget_threshold", "over_budget"}


def test_price_checks_are_throttled_per_student(app, real_cache, user, budget, provider):
    cheapest, dearest = cheapest_and_dearest(provider, user)
    add_milk(user, dearest)
    assert notifications.evaluate(user, prices=True, provider=provider)
    ground = notifications.evaluate(user, prices=True, provider=_Broken(RuntimeError("would be called")))
    assert ground == []  # not called at all: the check is not due yet
    cache.clear()
    with pytest.raises(RuntimeError):
        notifications.evaluate(user, prices=True, provider=_Broken(RuntimeError("called again")))


def test_a_student_with_an_empty_list_costs_no_provider_calls(user, budget):
    assert notifications.check_prices(user, _Broken(RuntimeError("never called"))) == []


# --------------------------------------------------------------------------------------- reading and marking
def seed_three(user):
    now = datetime.now(timezone.utc)
    rows = []
    for index, (read, age) in enumerate([(False, 30), (True, 5), (False, 10)]):
        rows.append(
            Notification(
                UserId=user.UserId,
                Type="over_budget",
                Title=f"n{index}",
                Message="m",
                IsRead=read,
                CreatedOn=now - timedelta(minutes=age),
            )
        )
    db.session.add_all(rows)
    db.session.commit()


def test_unread_come_first_then_newest(user):
    seed_three(user)
    assert [n.Title for n in notifications.list_for_user(user.UserId)] == ["n2", "n0", "n1"]
    assert notifications.unread_count(user.UserId) == 2


def test_mark_read_only_touches_the_owners_notification(user, make_user):
    other = make_user()
    seed_three(user)
    row = notifications.list_for_user(user.UserId)[0]
    assert notifications.mark_read(other.UserId, row.NotificationId) is None
    assert notifications.unread_count(user.UserId) == 2
    assert notifications.mark_read(user.UserId, row.NotificationId).IsRead is True
    assert notifications.unread_count(user.UserId) == 1


def test_mark_all_read(user):
    seed_three(user)
    assert notifications.mark_all_read(user.UserId) == 2
    assert notifications.unread_count(user.UserId) == 0


@pytest.mark.parametrize(
    "seconds, text",
    [(5, "just now"), (120, "2 min ago"), (7200, "2 h ago"), (86400, "1 day ago"), (3 * 86400, "3 days ago")],
)
def test_time_ago(seconds, text):
    now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
    assert notifications.time_ago(now - timedelta(seconds=seconds), now) == text


def test_time_ago_falls_back_to_the_date_after_a_week():
    now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
    assert "2026" in notifications.time_ago(now - timedelta(days=20), now)
    assert notifications.time_ago(None) == ""


# ---------------------------------------------------------------------------------------------- the endpoints
@pytest.fixture
def signed_in(client, user, login):
    login(user)
    return client


def test_the_endpoints_need_a_signed_in_student(client):
    assert client.get("/api/notifications").status_code == 401
    assert client.post("/api/notifications/NTF-x/read").status_code == 401
    assert client.post("/api/notifications/read-all").status_code == 401


def test_list_endpoint_returns_unread_first_with_the_unread_count(signed_in, user):
    seed_three(user)
    body = signed_in.get("/api/notifications").get_json()
    assert body["unread"] == 2
    assert [n["title"] for n in body["notifications"]] == ["n2", "n0", "n1"]
    first = body["notifications"][0]
    assert set(first) == {"id", "title", "message", "type", "level", "icon", "is_read", "url", "created_on", "ago"}
    assert first["is_read"] is False and first["level"] == "danger"


def test_list_endpoint_filters_and_limits(signed_in, user):
    seed_three(user)
    assert len(signed_in.get("/api/notifications?unread=1").get_json()["notifications"]) == 2
    assert len(signed_in.get("/api/notifications?limit=1").get_json()["notifications"]) == 1
    assert len(signed_in.get("/api/notifications?limit=abc").get_json()["notifications"]) == 3
    assert len(signed_in.get("/api/notifications?limit=-5").get_json()["notifications"]) == 1


def test_list_endpoint_runs_the_rules_first(signed_in, user, budget):
    spend(user, budget, 80)
    body = signed_in.get("/api/notifications").get_json()
    assert [n["title"] for n in body["notifications"]] == ["75% of your budget used"]
    assert body["unread"] == 1


def test_list_endpoint_survives_a_broken_rule(signed_in, user, monkeypatch):
    seed_three(user)
    monkeypatch.setattr(notifications, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    response = signed_in.get("/api/notifications")
    assert response.status_code == 200 and response.get_json()["unread"] == 2


def test_read_endpoint(signed_in, user):
    seed_three(user)
    target = signed_in.get("/api/notifications").get_json()["notifications"][0]
    body = signed_in.post(f"/api/notifications/{target['id']}/read").get_json()
    assert body["notification"]["is_read"] is True and body["unread"] == 1
    assert signed_in.post(f"/api/notifications/{target['id']}/read").status_code == 200  # harmless to repeat


def test_read_endpoint_hides_other_students_notifications(signed_in, make_user):
    other = make_user()
    row = notifications.notify(other.UserId, "over_budget", "secret", "m")
    db.session.commit()
    response = signed_in.post(f"/api/notifications/{row.NotificationId}/read")
    assert response.status_code == 404 and "secret" not in response.get_data(as_text=True)
    assert db.session.get(Notification, row.NotificationId).IsRead is False
    assert signed_in.post("/api/notifications/NTF-does-not-exist/read").status_code == 404


def test_read_all_endpoint(signed_in, user):
    seed_three(user)
    assert signed_in.post("/api/notifications/read-all").get_json() == {"changed": 2, "unread": 0}


def test_one_student_never_sees_another_students_notifications(signed_in, make_user):
    other = make_user()
    notifications.notify(other.UserId, "over_budget", "not yours", "m")
    db.session.commit()
    assert signed_in.get("/api/notifications").get_json() == {"unread": 0, "notifications": []}


# ------------------------------------------------------------------------------------------------ the bell
def test_the_bell_shows_the_unread_count_and_the_notifications(signed_in, user, budget):
    spend(user, budget, 80)
    html = signed_in.get("/list").get_data(as_text=True)
    assert "75% of your budget used" in html
    assert 'data-bell-badge aria-hidden="true" >1<' in html or ">1</span>" in html
    assert "Mark all as read" in html and "notifications.js" in html


def test_an_empty_bell_says_all_caught_up_and_hides_the_badge(signed_in):
    html = signed_in.get("/list").get_data(as_text=True)
    assert "You are all caught up." in html
    assert 'data-bell-badge aria-hidden="true" hidden' in html


def test_the_bell_escapes_what_it_shows(signed_in, user, budget):
    notifications.notify(user.UserId, "price_drop", "<img src=x onerror=alert(1)>", "<script>alert(2)</script>")
    db.session.commit()
    html = signed_in.get("/list").get_data(as_text=True)
    assert "<script>alert(2)</script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html


def test_the_bell_script_is_not_loaded_for_visitors(client):
    assert "notifications.js" not in client.get("/login").get_data(as_text=True)


def test_more_than_nine_unread_shows_9_plus(signed_in, user):
    for n in range(12):
        notifications.notify(user.UserId, "over_budget", f"t{n}", "m")
    db.session.commit()
    assert ">9+<" in signed_in.get("/list").get_data(as_text=True)


# ------------------------------------------------------------------------------------------------------ the CLI
def test_notify_command_runs_for_active_students_only(app, user, make_user, budget):
    inactive = make_user(IsActive=False)
    other_budget = budgets.create_budget(inactive.UserId, "Inactive", [("Grocery", D("100"))])
    spend(inactive, other_budget, 80)
    spend(user, budget, 80)
    result = app.test_cli_runner().invoke(args=["notify"])
    assert result.exit_code == 0 and "1 notification(s) created." in result.output
    assert titles(inactive.UserId) == []


def test_evaluate_everyone_carries_on_after_one_students_failure(app, user, make_user, budget, monkeypatch):
    second = make_user()
    second_budget = budgets.create_budget(second.UserId, "Two", [("Grocery", D("100"))])
    spend(user, budget, 80)
    spend(second, second_budget, 80)
    real = notifications.evaluate

    def flaky(who, **kwargs):
        if who.UserId == user.UserId:
            raise RuntimeError("boom")
        return real(who, **kwargs)

    monkeypatch.setattr(notifications, "evaluate", flaky)
    assert notifications.evaluate_everyone() == 1
    assert titles(second.UserId) != []
