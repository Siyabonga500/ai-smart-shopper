"""Step 20: ``flask seed-demo`` (app/services/demo.py) builds three believable students."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import Budget, ListItem, ShoppingList, User
from app.services import budgets, demo, history
from app.utils.dates import as_utc, month_key, previous_months, today_sast, utcnow
from app.utils.passwords import password_problems

THABO, NOMVULA, SIPHO = "thabo.demo@example.com", "nomvula.demo@example.com", "sipho.demo@example.com"


def by_email(email):
    return db.session.scalar(select(User).where(User.Email == email))


@pytest.fixture
def seeded(app):
    return demo.seed_demo()


def test_the_demo_password_meets_the_password_policy():
    assert password_problems(demo.DEMO_PASSWORD) == []


def test_three_students_are_created_and_can_sign_in(app, seeded, client):
    assert sorted(user.Email for user in seeded.created) == sorted([THABO, NOMVULA, SIPHO])
    assert seeded.trips == 18 and seeded.items > 100 and not seeded.skipped
    for email in (THABO, NOMVULA, SIPHO):
        assert by_email(email).check_password(demo.DEMO_PASSWORD)
    response = client.post("/login", data={"Email": THABO, "Password": demo.DEMO_PASSWORD})
    assert response.status_code == 302


def test_each_student_has_six_finished_months_and_one_active_budget(app, seeded):
    expected = previous_months(today_sast(), 7)[:-1]
    for email in (THABO, NOMVULA, SIPHO):
        user = by_email(email)
        closed = db.session.scalars(
            select(Budget).where(Budget.UserId == user.UserId, Budget.IsActive.is_(False))
        ).all()
        assert sorted(month_key(b.DateCreated) for b in closed) == expected
        assert all(month_key(b.ClosedDate) == month_key(b.DateCreated) for b in closed)
        assert all(as_utc(b.ClosedDate) <= utcnow() for b in closed)  # nothing is dated in the future
        assert budgets.get_active_budget(user.UserId) is not None
        assert (
            db.session.scalar(
                select(func.count()).select_from(Budget).where(Budget.UserId == user.UserId, Budget.IsActive.is_(True))
            )
            == 1
        )


def test_past_trips_are_complete_purchases_inside_their_budget(app, seeded):
    for email in (THABO, NOMVULA, SIPHO):
        user = by_email(email)
        trips = history.find_trips(user.UserId, history.parse_filters({})[0], page_size=50).trips
        assert len(trips) == 6
        for trip in trips:
            budget = db.session.get(Budget, db.session.get(ShoppingList, trip.id).BudgetId)
            assert 0 < trip.total <= budget.TotalAmount
        items = db.session.scalars(
            select(ListItem).join(ShoppingList).where(ListItem.UserId == user.UserId, ShoppingList.IsActive.is_(False))
        ).all()
        assert items and all(i.IsPurchased and i.PurchasedDate for i in items)
        assert all(i.UnitCost > 0 and i.StoreName and i.BarCode for i in items)


def test_thabo_is_comfortably_inside_a_budget_of_individual_categories(app, seeded):
    budget = budgets.get_active_budget(by_email(THABO).UserId)
    position = budgets.position(budget)
    assert {s.Category for s in budget.sub_budgets} == {"Grocery", "Toiletries"}
    assert 0 < position.percent_used < 75 and not position.is_over
    assert budgets.nsfas_notice(budget.TotalAmount)["warning"] is None


def test_nomvula_has_one_combined_budget_close_to_its_limit(app, seeded):
    budget = budgets.get_active_budget(by_email(NOMVULA).UserId)
    assert [s.Category for s in budget.sub_budgets] == ["Combined"]
    position = budgets.position(budget)
    assert 75 <= position.percent_used <= 100 and not position.is_over
    stores = {item.StoreName for item in budgets.list_items(budgets.get_active_list(budget))}
    assert len(stores) >= 2  # a trip with more than one stop, so the route means something


def test_sipho_is_above_the_nsfas_reference_and_over_budget(app, seeded, client, login):
    user = by_email(SIPHO)
    budget = budgets.get_active_budget(user.UserId)
    assert budget.TotalAmount == 1900 and budgets.nsfas_notice(budget.TotalAmount)["warning"]
    assert budgets.position(budget).is_over
    login(user)
    page = client.get("/list").get_data(as_text=True)
    assert 'href="/list/summary"' not in page and "disabled" in page.split("Proceed to Summary")[0][-200:]


def test_the_pages_render_for_every_demo_student(app, seeded, client, login):
    for email in (THABO, NOMVULA, SIPHO):
        login(by_email(email))
        for path in ("/dashboard", "/budget", "/list", "/history", "/search", "/api/notifications"):
            assert client.get(path).status_code == 200, (email, path)


def test_running_it_again_changes_nothing_and_reset_rebuilds(app, seeded):
    before = db.session.scalar(select(func.count()).select_from(ListItem))
    again = demo.seed_demo()
    assert again.created == [] and sorted(again.skipped) == sorted([THABO, NOMVULA, SIPHO])
    assert db.session.scalar(select(func.count()).select_from(ListItem)) == before

    totals = {b.Title + b.UserId: b.UsedAmount for b in db.session.scalars(select(Budget))}
    rebuilt = demo.seed_demo(reset=True)
    assert len(rebuilt.created) == 3
    assert db.session.scalar(select(func.count()).select_from(User)) == 3
    assert db.session.scalar(select(func.count()).select_from(ListItem)) == before
    assert sorted(totals.values()) == sorted(
        b.UsedAmount for b in db.session.scalars(select(Budget))
    )  # same seed, same data


def test_reset_leaves_other_students_alone(app, make_user):
    other = make_user(email="someone.else@example.com")
    demo.seed_demo()
    demo.seed_demo(reset=True)
    assert db.session.get(User, other.UserId) is not None


def test_a_month_boundary_date_still_seeds(app):
    result = demo.seed_demo(today=date(2026, 1, 1))  # history crosses the new year
    assert result.trips == 18
    years = {month_key(b.DateCreated)[0] for b in db.session.scalars(select(Budget).where(Budget.IsActive.is_(False)))}
    assert years == {2025}


# ---------------------------------------------------------------------------------------------------------- the CLI
def test_cli_seeds_and_prints_the_sign_in_details(app):
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code == 0, result.output
    assert (
        THABO in result.output and demo.DEMO_PASSWORD in result.output and "Created 3 demo student(s)" in result.output
    )
    again = app.test_cli_runner().invoke(args=["seed-demo"])
    assert again.exit_code == 0 and "already exists" in again.output


def test_cli_refuses_to_seed_a_production_site(app):
    app.config["CONFIG_NAME"] = "production"
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code == 1 and "production" in result.output
    assert by_email(THABO) is None
    assert app.test_cli_runner().invoke(args=["seed-demo", "--allow-production"]).exit_code == 0


# ----------------------------------------------------------------------------- safety (found in the independent review)
def test_seeding_never_overwrites_a_store_an_admin_edited(app):
    from app.cli import add_missing_stores
    from app.data.durban_stores import DURBAN_STORES
    from app.models import Store

    add_missing_stores(DURBAN_STORES)
    store = db.session.scalar(select(Store).order_by(Store.Slug))
    store.Name, store.Phone = "Renamed by an admin", "031 000 0000"
    db.session.commit()
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code == 0, result.output
    db.session.refresh(store)
    assert (store.Name, store.Phone) == ("Renamed by an admin", "031 000 0000")
    assert add_missing_stores(DURBAN_STORES) == 0


def test_reset_will_not_remove_the_only_admin(app, make_user):
    demo.seed_demo()
    by_email(THABO).IsAdmin = True
    db.session.commit()
    result = app.test_cli_runner().invoke(args=["seed-demo", "--reset"])
    assert result.exit_code == 1 and "only active admin" in result.output
    assert by_email(THABO) is not None and by_email(THABO).IsAdmin  # nothing was deleted

    make_user(email="boss@example.com", IsAdmin=True)  # now someone else can run the admin
    assert app.test_cli_runner().invoke(args=["seed-demo", "--reset"]).exit_code == 0
    assert by_email(THABO) is not None and not by_email(THABO).IsAdmin


def test_a_failed_seed_leaves_no_half_built_student(app, monkeypatch):
    calls = {"n": 0}
    real = demo._past_month

    def explode_on_the_third_month(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(demo, "_past_month", explode_on_the_third_month)
    with pytest.raises(demo.DemoError, match="Nothing was left half-made"):
        demo.seed_demo()
    assert by_email(THABO) is None
    assert db.session.scalar(select(func.count()).select_from(Budget)) == 0
    monkeypatch.setattr(demo, "_past_month", real)
    assert len(demo.seed_demo().created) == 3  # a clean retry works
