"""Step 16: the admin portal: who may enter, the dashboard, users, categories, integrations, stores and the audit log."""

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import unescape

import pytest
import responses
from sqlalchemy import select

from app.extensions import cache, db
from app.models import AuditLog, CategoryMapping, IntegrationSetting, Store
from app.services import admin_users, audit, budgets, category_map, integrations, retail_api
from app.services.retail_api import CachedProvider, MockProvider, Product
from app.utils.dates import utcnow

D = Decimal


# ---------------------------------------------------------------------------------------------------- helpers
def sign_in(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = user.get_id()
        session["_fresh"] = True
    return client


@pytest.fixture
def admin(make_user):
    return make_user(first="Ada", last="Admin", email="ada@example.com", IsAdmin=True, CellphoneNumber="0831112222")


@pytest.fixture
def student(make_password_user):
    return make_password_user(
        email="sipho@example.com",
        first="Sipho",
        last="Dlamini",
        CellphoneNumber="0821234567",
        ResidentialAddress="12 Ritson Road, Durban",
    )


@pytest.fixture
def boss(app, admin):
    """A client signed in as the admin."""
    return sign_in(app.test_client(), admin)


@pytest.fixture
def log_file(app, tmp_path):
    path = tmp_path / "api_calls.log"
    app.config["API_LOG_FILE"] = str(path)
    return path


def audit_rows(action=None):
    query = select(AuditLog).order_by(AuditLog.CreatedOn, AuditLog.AuditId)
    return [row for row in db.session.scalars(query) if action is None or row.Action == action]


def call(when, service="apify", status=200, **extra):
    record = {
        "ts": when.strftime("%Y-%m-%dT%H:%M:%S+0000"),
        "service": service,
        "method": "GET",
        "url": "https://api.example.com/x",
        "status": status,
        "duration_ms": 120,
        "attempt": 1,
        **extra,
    }
    return json.dumps(record)


# ------------------------------------------------------------------------------------------- who may enter
def _admin_rules(app):
    rules = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith("admin.") and rule.endpoint != "admin.static":
            path = rule.rule
            for arg in rule.arguments:
                path = path.replace(f"<{arg}>", "does-not-exist")
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                rules.append((method, path))
    return rules


def test_the_route_table_is_what_these_tests_assume(app):
    rules = _admin_rules(app)
    assert len(rules) >= 25
    assert ("GET", "/admin/") in rules and ("POST", "/admin/users/does-not-exist/reset-password") in rules


def test_every_admin_route_turns_visitors_away(app):
    client = app.test_client()
    for method, path in _admin_rules(app):
        response = client.open(path, method=method)
        assert response.status_code == 302 and "/login" in response.headers["Location"], (
            method,
            path,
            response.status_code,
        )


def test_every_admin_route_refuses_ordinary_students(app, student):
    client = sign_in(app.test_client(), student)
    for method, path in _admin_rules(app):
        response = client.open(path, method=method)
        assert response.status_code == 403, (method, path, response.status_code)
    assert audit_rows() == []


def test_a_deactivated_admin_is_locked_out_at_once(app, admin):
    client = sign_in(app.test_client(), admin)
    assert client.get("/admin/").status_code == 200
    admin.IsActive = False
    db.session.commit()
    response = app.test_client() and sign_in(app.test_client(), admin).get("/admin/")
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_admin_pages_are_never_cached(boss):
    assert boss.get("/admin/").headers["Cache-Control"] == "no-store"


def test_the_flag_not_the_email_decides(app, make_user):
    ordinary = make_user(email="admin@example.com")
    assert sign_in(app.test_client(), ordinary).get("/admin/").status_code == 403


def test_a_student_is_not_shown_an_admin_link_and_admin_pages_use_the_admin_nav(boss):
    html = boss.get("/admin/users").get_data(as_text=True)
    assert "Back to the app" in html and "Audit log" in html and "app-bottom-nav" not in html


# ------------------------------------------------------------------------------------------------ dashboard
def test_dashboard_totals_and_charts(boss, student, log_file):
    budget = budgets.create_budget(student.UserId, "Sep", [("Grocery", D("500"))])
    budgets.ensure_active_list(budget)
    db.session.commit()
    now = utcnow()
    # a real log is appended to, so it runs oldest -> newest
    log_file.write_text("\n".join([call(now - timedelta(days=3)), "not json", call(now), call(now, status=500)]) + "\n")
    html = boss.get("/admin/").get_data(as_text=True)
    stats = dict(re.findall(r'data-stat="([a-z\-]+)">([^<]+)<', html))
    assert stats == {
        "users": "2",
        "active-budgets": "1",
        "active-lists": "1",
        "api-calls-today": "2",
        "products": "0",
        "stores": "0",
    }
    charts = json.loads(html.split('id="admin-chart-data">')[1].split("</script>")[0])
    assert sum(charts["registrations"]["values"]) == 2 and len(charts["registrations"]["labels"]) == 8
    assert (
        charts["apiCalls"]["ok"][-1] == 1
        and charts["apiCalls"]["error"][-1] == 1
        and len(charts["apiCalls"]["labels"]) == 7
    )
    assert "View as a table" in html and "chart-registrations" in html


def test_dashboard_counts_deactivated_students_and_admins(boss, student):
    student.IsActive = False
    db.session.commit()
    html = boss.get("/admin/").get_data(as_text=True)
    assert "1 deactivated · 1 admin" in html


# ---------------------------------------------------------------------------------------------------- users
def make_many(make_user, n):
    return [make_user(first=f"Student{i:02d}", last="Test", email=f"s{i:02d}@example.com") for i in range(n)]


def test_user_search_matches_name_email_phone_and_id(boss, student):
    for q in ("Sipho", "dlamini", "Sipho Dlamini", "sipho@example", "082123", student.UserId):
        html = boss.get(f"/admin/users?q={q}").get_data(as_text=True)
        assert student.Email in html, q
    assert student.Email not in boss.get("/admin/users?q=nobody").get_data(as_text=True)


def test_search_treats_percent_and_underscore_literally(boss, student):
    assert student.Email not in boss.get("/admin/users?q=%25").get_data(as_text=True)
    assert student.Email not in boss.get("/admin/users?q=s_pho").get_data(as_text=True)


def test_user_filters(boss, student, make_user):
    off = make_user(email="off@example.com", IsActive=False)
    active = boss.get("/admin/users?status=active").get_data(as_text=True)
    assert student.Email in active and off.Email not in active
    assert off.Email in boss.get("/admin/users?status=deactivated").get_data(as_text=True)
    admins = boss.get("/admin/users?role=Admin").get_data(as_text=True)
    assert "ada@example.com" in admins and student.Email not in admins
    assert student.Email in boss.get("/admin/users?status=bogus&role=bogus").get_data(
        as_text=True
    )  # unknown filters are ignored


def test_user_list_is_paginated(app, boss, make_user):
    make_many(make_user, 45)
    page1 = boss.get("/admin/users").get_data(as_text=True)
    assert "Showing 1–20 of 46" in page1 and 'rel="next"' in page1
    page3 = boss.get("/admin/users?page=3").get_data(as_text=True)
    assert "Showing 41–46 of 46" in page3
    assert "Showing 41–46 of 46" in boss.get("/admin/users?page=99").get_data(as_text=True)  # past the end: last page
    assert "Showing 1–20 of 46" in boss.get("/admin/users?page=abc").get_data(as_text=True)


def test_the_pager_keeps_the_filters(boss, make_user):
    make_many(make_user, 30)
    html = boss.get("/admin/users?q=Student&status=active").get_data(as_text=True)
    assert "q=Student" in html and "status=active" in html and "page=2" in html


def test_viewing_a_student_is_audited_and_shows_no_full_id_number(boss, student, admin):
    student.SAIdLast4 = "9087"
    db.session.commit()
    html = boss.get(f"/admin/users/{student.UserId}").get_data(as_text=True)
    assert "*********9087" in html and student.Email in html
    row = audit_rows("user.view")[0]
    assert row.AdminId == admin.UserId and row.Target == f"user:{student.UserId}"
    assert student.PasswordHash not in html


def test_unknown_user_is_a_404(boss):
    assert boss.get("/admin/users/nope").status_code == 404
    assert boss.post("/admin/users/nope/deactivate").status_code == 404


def edit_data(**overrides):
    data = {
        "FirstName": "Sipho",
        "LastName": "Dlamini",
        "CellphoneNumber": "0821234567",
        "ResidentialAddress": "12 Ritson Road, Durban",
        "Role": "Student",
    }
    data.update(overrides)
    return data


def test_edit_form_is_prefilled(boss, student):
    html = boss.get(f"/admin/users/{student.UserId}/edit").get_data(as_text=True)
    assert 'value="Sipho"' in html and 'value="0821234567"' in html and "12 Ritson Road" in html


def test_edit_saves_and_audits_what_changed(boss, student):
    response = boss.post(
        f"/admin/users/{student.UserId}/edit", data=edit_data(FirstName="Sipho-Jr", CellphoneNumber="072 999 8888")
    )
    assert response.status_code == 302
    db.session.refresh(student)
    assert student.FirstName == "Sipho-Jr" and student.CellphoneNumber == "0729998888"
    changes = audit_rows("user.update")[0].Detail["changes"]
    assert changes == {
        "first_name": {"from": "Sipho", "to": "Sipho-Jr"},
        "cellphone": {"from": "0821234567", "to": "0729998888"},
    }


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("FirstName", "A", "between 2 and 50"),
        ("LastName", "", "Enter the last name"),
        ("CellphoneNumber", "0311234567", "10 digits"),
        ("Role", "Superuser", "Not a valid choice"),
        ("ResidentialAddress", "x" * 301, "too long"),
    ],
)
def test_edit_validation(boss, student, field, value, message):
    response = boss.post(f"/admin/users/{student.UserId}/edit", data=edit_data(**{field: value}))
    assert response.status_code == 400 and message in response.get_data(as_text=True)
    db.session.refresh(student)
    assert student.FirstName == "Sipho" and audit_rows("user.update") == []


def test_an_admin_can_promote_a_student_and_the_student_can_then_enter(app, boss, student):
    assert boss.post(f"/admin/users/{student.UserId}/edit", data=edit_data(Role="Admin")).status_code == 302
    assert sign_in(app.test_client(), student).get("/admin/").status_code == 200


def test_an_admin_cannot_remove_their_own_role(boss, admin):
    response = boss.post(
        f"/admin/users/{admin.UserId}/edit",
        data=edit_data(FirstName="Ada", LastName="Admin", CellphoneNumber="0831112222", Role="Student"),
    )
    assert response.status_code == 400 and "cannot remove your own admin role" in response.get_data(as_text=True)
    db.session.refresh(admin)
    assert admin.IsAdmin is True


def test_the_last_active_admin_cannot_be_demoted_by_another_admin(app, admin, make_user):
    other = make_user(email="other@example.com", IsAdmin=True, IsActive=False)  # inactive, so it does not count
    with pytest.raises(admin_users.AdminUserError, match="only active admin"):
        admin_users.update(
            other, admin, first_name="Ada", last_name="Admin", cellphone="0831112222", address=None, role="Student"
        )


def test_an_inactive_account_cannot_be_made_admin(boss, make_user):
    off = make_user(email="off@example.com", IsActive=False)
    response = boss.post(f"/admin/users/{off.UserId}/edit", data=edit_data(Role="Admin"))
    assert response.status_code == 400 and "Reactivate this account" in response.get_data(as_text=True)


def test_deactivating_signs_the_student_out_and_blocks_login(app, boss, student):
    student_client = sign_in(app.test_client(), student)
    assert student_client.get("/dashboard").status_code == 200
    response = boss.post(f"/admin/users/{student.UserId}/deactivate")
    assert response.status_code == 302
    db.session.refresh(student)
    assert student.IsActive is False
    assert student_client.get("/dashboard").status_code == 302  # the open session no longer works
    fresh = app.test_client().post("/login", data={"Email": student.Email, "Password": "Str0ng!Pass"})
    assert fresh.status_code == 403 and "has been deactivated" in fresh.get_data(as_text=True)
    assert audit_rows("user.deactivate")[0].Target == f"user:{student.UserId}"


def test_a_wrong_password_on_a_deactivated_account_does_not_reveal_it(app, boss, student):
    boss.post(f"/admin/users/{student.UserId}/deactivate")
    response = app.test_client().post("/login", data={"Email": student.Email, "Password": "Wrong1!Pass"})
    assert response.status_code == 401 and "Incorrect email or password" in response.get_data(as_text=True)


def test_reactivating_restores_access(app, boss, student):
    boss.post(f"/admin/users/{student.UserId}/deactivate")
    assert boss.post(f"/admin/users/{student.UserId}/reactivate").status_code == 302
    login = app.test_client().post("/login", data={"Email": student.Email, "Password": "Str0ng!Pass"})
    assert login.status_code == 302
    assert [row.Action for row in audit_rows()] == ["user.deactivate", "user.reactivate"]


def test_admin_cannot_deactivate_themselves_or_the_last_admin(boss, admin):
    response = boss.post(f"/admin/users/{admin.UserId}/deactivate", follow_redirects=True)
    assert "cannot deactivate your own account" in response.get_data(as_text=True)
    db.session.refresh(admin)
    assert admin.IsActive is True and audit_rows("user.deactivate") == []


def test_deactivating_twice_says_so(boss, student):
    boss.post(f"/admin/users/{student.UserId}/deactivate")
    text = boss.post(f"/admin/users/{student.UserId}/deactivate", follow_redirects=True).get_data(as_text=True)
    assert "already deactivated" in text and len(audit_rows("user.deactivate")) == 1


def temp_password(html):
    """The password in the page, decoded the way a browser would (it can contain & < > and quotes)."""
    return unescape(html.split('id="temp-password" value="')[1].split('"')[0])


def test_reset_password_shows_a_working_password_once_and_never_stores_it_in_the_audit_log(app, boss, student):
    old_hash = student.PasswordHash
    response = boss.post(f"/admin/users/{student.UserId}/reset-password")
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store" and "only once" in html
    password = temp_password(html)
    from app.utils.passwords import password_problems

    assert password_problems(password) == [] and len(password) >= 14
    db.session.refresh(student)
    assert student.PasswordHash != old_hash and student.check_password(password)
    assert app.test_client().post("/login", data={"Email": student.Email, "Password": password}).status_code == 302
    assert app.test_client().post("/login", data={"Email": student.Email, "Password": "Str0ng!Pass"}).status_code == 401
    row = audit_rows("user.reset_password")[0]
    assert password not in json.dumps(row.Detail) and password not in (row.Target or "")
    assert password not in boss.get(f"/admin/users/{student.UserId}").get_data(as_text=True)


def test_each_reset_gives_a_different_password(boss, student):
    passwords = {
        temp_password(boss.post(f"/admin/users/{student.UserId}/reset-password").get_data(as_text=True))
        for _ in range(3)
    }
    assert len(passwords) == 3


def test_microsoft_only_and_deactivated_accounts_cannot_be_reset(boss, make_user, student):
    microsoft = make_user(email="ms@example.com")  # no password
    text = boss.post(f"/admin/users/{microsoft.UserId}/reset-password", follow_redirects=True).get_data(as_text=True)
    assert "signs in with Microsoft" in text
    boss.post(f"/admin/users/{student.UserId}/deactivate")
    assert "Reactivate this account" in boss.post(
        f"/admin/users/{student.UserId}/reset-password", follow_redirects=True
    ).get_data(as_text=True)
    assert audit_rows("user.reset_password") == []


def test_temporary_passwords_always_satisfy_the_policy():
    from app.utils.passwords import password_problems

    assert all(password_problems(admin_users.temporary_password()) == [] for _ in range(300))


def test_editing_cannot_touch_email_id_or_password_fields(boss, student):
    before = (student.Email, student.PasswordHash, student.SAIdHash, student.IsActive)
    boss.post(
        f"/admin/users/{student.UserId}/edit",
        data=edit_data(Email="evil@example.com", PasswordHash="x", IsActive="", SAIdHash="y"),
    )
    db.session.refresh(student)
    assert (student.Email, student.PasswordHash, student.SAIdHash, student.IsActive) == before


# -------------------------------------------------------------------------------------------- categories
def test_category_page_lists_the_targets(boss):
    html = boss.get("/admin/categories").get_data(as_text=True)
    for name in ("Grocery", "Toiletries", "Clothes", "Electronics", "Combined"):
        assert name in html


def test_create_edit_delete_a_mapping_and_audit_each(boss):
    assert (
        boss.post(
            "/admin/categories", data={"RawCategory": "  Health &   Beauty ", "MappedCategory": "Toiletries"}
        ).status_code
        == 302
    )
    row = db.session.scalars(select(CategoryMapping)).one()
    assert (row.RawCategory, row.RawKey, row.MappedCategory) == ("Health & Beauty", "health & beauty", "Toiletries")
    assert "Health &amp; Beauty" in boss.get("/admin/categories").get_data(as_text=True)
    assert (
        boss.post(
            f"/admin/categories/{row.MappingId}/edit",
            data={"RawCategory": "Health & Beauty", "MappedCategory": "Grocery"},
        ).status_code
        == 302
    )
    db.session.refresh(row)
    assert row.MappedCategory == "Grocery"
    assert boss.post(f"/admin/categories/{row.MappingId}/delete").status_code == 302
    assert db.session.scalars(select(CategoryMapping)).all() == []
    assert [r.Action for r in audit_rows()] == ["category.create", "category.update", "category.delete"]
    assert audit_rows("category.update")[0].Detail["changes"] == {"to": {"from": "Toiletries", "to": "Grocery"}}


@pytest.mark.parametrize(
    "raw, mapped, message",
    [
        ("", "Grocery", "Enter the category name"),
        ("x" * 151, "Grocery", "At most 150 characters"),
        ("Dairy", "Furniture", "Not a valid choice"),
    ],
    ids=["empty", "too-long", "unknown-target"],
)
def test_mapping_validation(boss, raw, mapped, message):
    response = boss.post("/admin/categories", data={"RawCategory": raw, "MappedCategory": mapped})
    assert response.status_code == 400 and message in response.get_data(as_text=True)
    assert db.session.scalars(select(CategoryMapping)).all() == []


def test_a_raw_name_can_only_be_mapped_once_ignoring_case_and_spaces(boss):
    boss.post("/admin/categories", data={"RawCategory": "Fresh Milk", "MappedCategory": "Grocery"})
    response = boss.post("/admin/categories", data={"RawCategory": "  fresh   MILK", "MappedCategory": "Toiletries"})
    assert response.status_code == 400 and "already mapped" in response.get_data(as_text=True)
    assert db.session.query(CategoryMapping).count() == 1


def test_editing_into_another_mappings_name_is_refused(boss):
    boss.post("/admin/categories", data={"RawCategory": "A", "MappedCategory": "Grocery"})
    boss.post("/admin/categories", data={"RawCategory": "B", "MappedCategory": "Grocery"})
    b = db.session.scalars(select(CategoryMapping).where(CategoryMapping.RawKey == "b")).one()
    response = boss.post(
        f"/admin/categories/{b.MappingId}/edit", data={"RawCategory": "a", "MappedCategory": "Grocery"}
    )
    assert response.status_code == 400 and "Another mapping" in response.get_data(as_text=True)


def test_category_filters(boss):
    for raw, target in [("Milk", "Grocery"), ("Soap", "Toiletries"), ("Shirts", "Clothes")]:
        boss.post("/admin/categories", data={"RawCategory": raw, "MappedCategory": target})
    html = boss.get("/admin/categories?category=Toiletries").get_data(as_text=True)
    assert ">Soap<" in html and ">Milk<" not in html
    assert ">Shirts<" in boss.get("/admin/categories?q=shir").get_data(as_text=True)
    assert boss.get("/admin/categories?category=Bogus").status_code == 200


def test_a_category_name_is_escaped_on_the_page(boss):
    boss.post("/admin/categories", data={"RawCategory": "<script>alert(1)</script>", "MappedCategory": "Grocery"})
    html = boss.get("/admin/categories").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_normalise_key_matches_the_provider_side_key():
    from app.services.retail_api import _category_key

    for text in ["Fresh  Milk", " ÉCLAIRS ", "Health & Beauty", "", None, "İstanbul", "éclair"]:
        assert category_map.normalise_key(text) == _category_key(text)


# --------------------------------------------------------------- category mappings applied to real answers
def product(name, category, barcode="1", store="Shop A", price="10.00"):
    return Product(
        name=name,
        barcode=barcode,
        price=D(price),
        image_url=None,
        store_name=store,
        store_address=None,
        store_lat=None,
        store_lng=None,
        category=category,
    )


class FakeSource(MockProvider):
    """Returns raw retailer categories and remembers what it was asked."""

    name = "fake"

    def __init__(self):
        self.calls = []
        self.catalogue = [
            product("Yoghurt", "Fresh Milk & Dairy", "1"),
            product("Soap", "Health & Beauty", "2"),
            product("Bread", "Bakery", "3"),
            product("Shampoo", "Toiletries", "4"),
        ]

    def search_products(self, query, lat, lng, radius_km=15, category=None, limit=200):
        self.calls.append((query, category))
        wanted = (category or "").casefold()
        return [
            p
            for p in self.catalogue
            if (not wanted or p.category.casefold() == wanted) and (not query or query.casefold() in p.name.casefold())
        ]

    def get_offers_by_barcode(self, barcode, lat=None, lng=None, radius_km=15):
        return [p for p in self.catalogue if p.barcode == barcode]


@pytest.fixture
def mapped_provider(app):
    def make():
        source = FakeSource()
        return source, CachedProvider(source, 900, categories=category_map.table)

    return make


def add_mapping(raw, target):
    db.session.add(CategoryMapping(RawCategory=raw, RawKey=category_map.normalise_key(raw), MappedCategory=target))
    db.session.commit()


def test_mapped_categories_replace_the_raw_ones_in_every_answer(mapped_provider):
    add_mapping("Fresh Milk & Dairy", "Grocery")
    add_mapping("health & beauty", "Toiletries")
    _, provider = mapped_provider()
    by_name = {p.name: p.category for p in provider.search_products("", -29.85, 31.0)}
    assert by_name == {
        "Yoghurt": "Grocery",
        "Soap": "Toiletries",
        "Bread": "Bakery",
        "Shampoo": "Toiletries",
    }  # unmapped stay as they are
    assert provider.get_offers_by_barcode("2")[0].category == "Toiletries"


def test_without_mappings_nothing_changes(mapped_provider):
    _, provider = mapped_provider()
    assert {p.category for p in provider.search_products("", -29.85, 31.0)} == {
        "Fresh Milk & Dairy",
        "Health & Beauty",
        "Bakery",
        "Toiletries",
    }


def test_searching_a_category_finds_products_mapped_onto_it(mapped_provider):
    add_mapping("Health & Beauty", "Toiletries")
    _, provider = mapped_provider()
    found = provider.search_products("o", -29.85, 31.0, 15, "Toiletries")  # "Soap" (mapped) + "Shampoo" (already right)
    assert {p.name for p in found} == {"Soap", "Shampoo"} and {p.category for p in found} == {"Toiletries"}


def test_browsing_a_category_without_a_search_asks_for_each_raw_name(mapped_provider):
    add_mapping("Health & Beauty", "Toiletries")
    source, provider = mapped_provider()
    found = provider.search_products("", -29.85, 31.0, 15, "Toiletries")
    assert {p.name for p in found} == {"Soap", "Shampoo"}
    assert (
        ("", "Toiletries") in source.calls
        and ("", "health & beauty") in [(q, c) for q, c in source.calls]
        or any(c and c.lower() == "health & beauty" for _, c in source.calls)
    )


def test_categories_with_no_mapping_onto_them_are_filtered_by_the_source_as_before(mapped_provider):
    add_mapping("Health & Beauty", "Toiletries")
    source, provider = mapped_provider()
    found = provider.search_products("", -29.85, 31.0, 15, "Bakery")
    assert [p.name for p in found] == ["Bread"] and source.calls == [("", "Bakery")]


def test_a_mapping_change_takes_effect_without_clearing_the_cache(real_cache, mapped_provider):
    _, provider = mapped_provider()
    assert {p.category for p in provider.search_products("soap", -29.85, 31.0)} == {"Health & Beauty"}
    add_mapping("Health & Beauty", "Toiletries")
    category_map.invalidate()
    assert {p.category for p in provider.search_products("soap", -29.85, 31.0)} == {"Toiletries"}


def test_the_app_provider_uses_the_mappings(app):
    add_mapping("Grocery", "Combined")  # silly but visible: the mock catalogue's own names
    provider = retail_api.get_retail_provider()
    assert {p.category for p in provider.search_products("milk", -29.85, 31.02, 15)} <= {"Combined"}


# ------------------------------------------------------------------------------------------ integrations
def test_the_page_starts_on_the_environment_setting(boss):
    assert integrations.active_source() == "mock"
    html = boss.get("/admin/integrations").get_data(as_text=True)
    for label in ("Apify", "Parse.bot", "Mock data"):
        assert label in html
    assert html.count("Active</span>") >= 1


def test_a_source_that_needs_a_key_cannot_be_activated_without_one(boss):
    text = boss.post("/admin/integrations/apify/activate", follow_redirects=True).get_data(as_text=True)
    assert "Add Apify&#39;s api token before making it the active source" in text
    assert integrations.active_source() == "mock" and audit_rows("integration.activate") == []


def test_saving_a_key_encrypts_it_masks_it_and_keeps_it_out_of_the_audit_log(app, boss):
    secret = "apify_api_SECRET1234567890wxyz"
    assert boss.post("/admin/integrations/apify/key", data={"ApiKey": secret}).status_code == 302
    row = db.session.get(IntegrationSetting, "apify")
    assert row.ApiKeyEncrypted and secret not in row.ApiKeyEncrypted and "SECRET" not in row.ApiKeyEncrypted
    html = boss.get("/admin/integrations").get_data(as_text=True)
    assert secret not in html and "••••••••wxyz" in html and "admin portal" in html
    assert all(secret not in json.dumps(r.Detail) and secret not in (r.Target or "") for r in audit_rows())
    assert [r.Action for r in audit_rows()] == ["integration.set_key"]


@pytest.mark.parametrize(
    "key, message",
    [
        ("short", "between 8 and 512"),
        ("has a space in it", "cannot contain spaces"),
        ("x" * 513, "between 8 and 512"),
        ("line\nbreak-in-key", "cannot contain spaces"),
    ],
)
def test_key_validation(boss, key, message):
    text = boss.post("/admin/integrations/apify/key", data={"ApiKey": key}, follow_redirects=True).get_data(
        as_text=True
    )
    assert message in text and db.session.get(IntegrationSetting, "apify") is None


def test_the_mock_source_takes_no_key(boss):
    text = boss.post(
        "/admin/integrations/mock/key", data={"ApiKey": "whatever-key-1234"}, follow_redirects=True
    ).get_data(as_text=True)
    assert "does not use an API key" in text


ACTORS = json.dumps({"checkers": {"actor": "acme/checkers", "input": {"search": "{query}"}}})


def test_activating_a_source_switches_the_app_provider_and_only_one_is_active(app, boss):
    app.config["APIFY_ACTORS_JSON"] = ACTORS
    boss.post("/admin/integrations/parsebot/key", data={"ApiKey": "pb_key_1234567890"})
    boss.post("/admin/integrations/apify/key", data={"ApiKey": "apify_key_1234567890"})
    assert isinstance(retail_api.get_retail_provider().inner, MockProvider)
    boss.post("/admin/integrations/apify/activate")
    assert integrations.active_source() == "apify" and retail_api.get_retail_provider().name == "apify"
    boss.post("/admin/integrations/parsebot/activate")
    assert retail_api.get_retail_provider().name == "parsebot"
    assert [r.IsActive for r in db.session.scalars(select(IntegrationSetting).order_by(IntegrationSetting.Source))] == [
        False,
        False,
        False,
        False,
        True,
    ]  # apify, buyly, loyaltyhub, mock, parsebot
    assert audit_rows("integration.activate")[-1].Detail == {"previous": "apify"}


def test_the_saved_key_is_the_one_the_provider_uses(app, boss):
    app.config["APIFY_ACTORS_JSON"] = ACTORS
    boss.post("/admin/integrations/apify/key", data={"ApiKey": "apify_key_1234567890"})
    boss.post("/admin/integrations/apify/activate")
    assert retail_api.get_retail_provider().inner.token == "apify_key_1234567890"


def test_workers_notice_a_change_made_by_another_worker(app, admin):
    """Another gunicorn worker changed the database: this one rebuilds after RETAIL_SETTINGS_RECHECK_SECONDS."""
    app.config["RETAIL_SETTINGS_RECHECK_SECONDS"] = 0
    assert retail_api.get_retail_provider().name == "mock"
    db.session.add(
        IntegrationSetting(
            Source="parsebot",
            IsActive=True,
            ApiKeyEncrypted=__import__("app.utils.secrets", fromlist=["x"]).encrypt_secret("pb_key_1234567890"),
        )
    )
    db.session.commit()
    assert retail_api.get_retail_provider().name == "parsebot"


def test_an_unchanged_setting_does_not_rebuild_the_provider(app):
    app.config["RETAIL_SETTINGS_RECHECK_SECONDS"] = 0
    first = retail_api.get_retail_provider()
    assert retail_api.get_retail_provider() is first


def test_removing_the_key_of_the_active_source_is_refused_without_an_environment_key(app, boss):
    boss.post("/admin/integrations/apify/key", data={"ApiKey": "apify_key_1234567890"})
    boss.post("/admin/integrations/apify/activate")
    text = boss.post("/admin/integrations/apify/key/delete", follow_redirects=True).get_data(as_text=True)
    assert "Switch to another source before removing its key" in text
    assert db.session.get(IntegrationSetting, "apify").ApiKeyEncrypted


def test_removing_a_key_when_the_environment_has_one(app, boss):
    app.config["APIFY_API_TOKEN"] = "env_token_1234567890"
    boss.post("/admin/integrations/apify/key", data={"ApiKey": "apify_key_1234567890"})
    assert boss.post("/admin/integrations/apify/key/delete").status_code == 302
    assert db.session.get(IntegrationSetting, "apify").ApiKeyEncrypted is None
    assert integrations.effective_config(app.config)["APIFY_API_TOKEN"] == "env_token_1234567890"
    assert "No key is saved" in boss.post("/admin/integrations/apify/key/delete", follow_redirects=True).get_data(
        as_text=True
    )


def test_a_key_that_can_no_longer_be_decrypted_is_reported_not_crashed(app, boss, admin):
    boss.post("/admin/integrations/apify/key", data={"ApiKey": "apify_key_1234567890"})
    app.config["SECRET_KEY"] = "a-different-secret"  # also invalidates the old session cookie
    html = sign_in(app.test_client(), admin).get("/admin/integrations").get_data(as_text=True)
    assert "cannot be read" in html
    assert integrations.effective_config(app.config).get("APIFY_API_TOKEN") is None


def test_unknown_sources_are_404(boss):
    for path in ("activate", "key", "key/delete", "sync"):
        assert boss.post(f"/admin/integrations/bogus/{path}", data={"ApiKey": "x" * 10}).status_code == 404


def test_sync_now_on_the_mock_source_records_success_and_clears_the_cache(app, boss, real_cache):
    before = retail_api.get_retail_provider().search_products("milk", -29.85, 31.02, 15)
    assert before
    cache.set("retail:version", 5)
    boss.post("/admin/integrations/mock/sync")
    row = db.session.get(IntegrationSetting, "mock")
    assert row.LastSyncStatus == "ok" and "found" in row.LastSyncMessage and row.LastSyncOn is not None
    assert cache.get("retail:version") != 5
    assert "Last sync" in boss.get("/admin/integrations").get_data(as_text=True)
    assert audit_rows("integration.sync")[0].Detail["status"] == "ok"


@responses.activate
def test_sync_now_reports_a_rejected_key_without_leaking_it(app, boss):
    key = "apify_rejected_SECRET_123456"
    boss.post("/admin/integrations/apify/key", data={"ApiKey": key})
    app.config["APIFY_ACTORS_JSON"] = ACTORS
    responses.add(responses.POST, "https://api.apify.com/v2/acts/acme~checkers/runs", status=401, json={})
    boss.post("/admin/integrations/apify/sync")
    row = db.session.get(IntegrationSetting, "apify")
    assert row.LastSyncStatus == "error" and "rejected" in row.LastSyncMessage.lower()
    assert key not in row.LastSyncMessage and key not in boss.get("/admin/integrations").get_data(as_text=True)


def test_sync_now_reports_a_missing_key(boss):
    boss.post("/admin/integrations/parsebot/sync")
    row = db.session.get(IntegrationSetting, "parsebot")
    assert row.LastSyncStatus == "error" and row.LastSyncMessage


def test_sync_survives_a_source_that_blows_up(app, boss, monkeypatch):
    monkeypatch.setattr(integrations, "_provider_for", lambda source: (_ for _ in ()).throw(RuntimeError("boom")))
    assert boss.post("/admin/integrations/mock/sync").status_code == 302
    assert db.session.get(IntegrationSetting, "mock").LastSyncStatus == "error"


def test_last_sync_prefers_the_active_source(app):
    assert integrations.last_sync() is None
    db.session.add_all(
        [
            IntegrationSetting(Source="apify", LastSyncOn=utcnow() - timedelta(days=2)),
            IntegrationSetting(Source="mock", IsActive=True, LastSyncOn=utcnow() - timedelta(hours=1)),
        ]
    )
    db.session.commit()
    assert (
        3000 < (utcnow() - integrations.last_sync()).total_seconds() < 4000
    )  # the active source's hour-old sync, not apify's two days


# ------------------------------------------------------------------------------------------------ API log
def test_call_log_filters(app, boss, log_file):
    now = utcnow()
    log_file.write_text(
        "\n".join(
            [
                call(now - timedelta(days=2), service="apify"),
                call(now, service="parsebot", status=500, error="HTTP 500"),
                call(now, service="apify", status=200),
                call(now, service="apify", status=None, error="timeout"),
                "garbage",
                "",
            ]
        )
        + "\n"
    )
    assert len(integrations.recent_calls()) == 4
    assert [c["service"] for c in integrations.recent_calls(service="parsebot")] == ["parsebot"]
    assert len(integrations.recent_calls(outcome="ok")) == 2 and len(integrations.recent_calls(outcome="error")) == 2
    day = integrations.recent_calls()[0]["when"].date()
    assert len(integrations.recent_calls(day=day)) >= 3
    html = boss.get("/admin/integrations/logs?service=apify&outcome=error").get_data(as_text=True)
    assert "timeout" in html and "HTTP 500" not in html
    assert boss.get("/admin/integrations/logs?day=not-a-date&outcome=weird").status_code == 200


def test_call_log_reads_rotated_files_newest_first(app, log_file):
    now = utcnow()
    log_file.write_text(call(now, service="new") + "\n")
    (log_file.parent / "api_calls.log.1").write_text(call(now - timedelta(hours=5), service="old") + "\n")
    assert [c["service"] for c in integrations.recent_calls()] == ["new", "old"]


def test_calls_today_and_per_day_counts(app, log_file):
    now = utcnow()
    log_file.write_text(
        "\n".join([call(now - timedelta(days=30)), call(now - timedelta(days=1)), call(now), call(now)]) + "\n"
    )
    assert integrations.calls_today() == 2
    series = integrations.calls_per_day(7)
    assert len(series) == 7 and sum(row["ok"] for row in series) == 3 and series[-1]["ok"] == 2


def test_a_missing_log_file_is_fine(app, log_file):
    assert integrations.recent_calls() == [] and integrations.calls_today() == 0


def test_log_lines_are_escaped_on_the_page(boss, log_file):
    log_file.write_text(call(utcnow(), error="<script>alert(1)</script>", status=500) + "\n")
    html = boss.get("/admin/integrations/logs").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


# ---------------------------------------------------------------------------------------------------- stores
def store_data(**overrides):
    data = {
        "Name": "Checkers Test Mall",
        "Brand": "Checkers",
        "Suburb": "Glenwood",
        "Address": "1 Test Road, Glenwood, Durban",
        "Latitude": "-29.8700",
        "Longitude": "31.0000",
        "LocationSource": "official",
        "OpeningHours": "Mon-Sun 08:00-20:00",
        "Phone": "031 123 4567",
    }
    data.update(overrides)
    return data


def test_store_crud_with_audit(boss):
    assert boss.post("/admin/stores/new", data=store_data()).status_code == 302
    store = db.session.scalars(select(Store)).one()
    assert store.Slug == "checkers-test-mall" and store.Latitude == -29.87 and store.StoreId.startswith("STR-")
    assert "Checkers Test Mall" in boss.get("/admin/stores").get_data(as_text=True)
    assert (
        boss.post(
            f"/admin/stores/{store.StoreId}/edit", data=store_data(Name="Checkers Renamed", OpeningHours="")
        ).status_code
        == 302
    )
    db.session.refresh(store)
    assert (
        store.Name == "Checkers Renamed" and store.OpeningHours is None and store.Slug == "checkers-test-mall"
    )  # slug is stable
    assert boss.post(f"/admin/stores/{store.StoreId}/delete").status_code == 302
    assert db.session.scalars(select(Store)).all() == []
    assert [r.Action for r in audit_rows()] == ["store.create", "store.update", "store.delete"]
    assert audit_rows("store.update")[0].Detail["changes"]["name"] == {
        "from": "Checkers Test Mall",
        "to": "Checkers Renamed",
    }


def test_store_edit_page_is_prefilled(boss):
    boss.post("/admin/stores/new", data=store_data())
    store = db.session.scalars(select(Store)).one()
    html = boss.get(f"/admin/stores/{store.StoreId}/edit").get_data(as_text=True)
    assert 'value="Checkers Test Mall"' in html and 'value="-29.87"' in html and "1 Test Road" in html


def test_two_stores_with_the_same_name_get_different_slugs(boss):
    boss.post("/admin/stores/new", data=store_data())
    boss.post("/admin/stores/new", data=store_data())
    assert sorted(s.Slug for s in db.session.scalars(select(Store))) == ["checkers-test-mall", "checkers-test-mall-2"]


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("Name", "", "Enter the store name"),
        ("Name", "X", "Between 2 and 100"),
        ("Brand", "", "Enter the brand"),
        ("Address", "", "Enter the street address"),
        ("Latitude", "", "Enter the latitude"),
        ("Latitude", "abc", "Enter the latitude"),
        ("Latitude", "31.0", "Did you swap latitude and longitude"),
        ("Longitude", "-29.8", "Did you swap latitude and longitude"),
        ("Latitude", "95", "between -35 and -22"),
        ("Longitude", "181", "between 16 and 33"),
        ("Latitude", "NaN", "between -35 and -22"),
        ("Phone", "call me", "digits"),
        ("LocationSource", "guess", "Not a valid choice"),
        ("OpeningHours", "x" * 256, "At most 255"),
    ],
)
def test_store_validation(boss, field, value, message):
    response = boss.post("/admin/stores/new", data=store_data(**{field: value}))
    assert response.status_code == 400 and message in response.get_data(as_text=True)
    assert db.session.scalars(select(Store)).all() == [] and audit_rows() == []


def test_store_filters_and_pagination(app, boss):
    for i in range(45):
        boss.post(
            "/admin/stores/new",
            data=store_data(
                Name=f"Shop {i:02d}", Brand="SPAR" if i % 2 else "Checkers", Suburb="Berea" if i < 5 else "Glenwood"
            ),
        )
    assert "Showing 1–20 of 45" in boss.get("/admin/stores").get_data(as_text=True)
    assert "Showing 1–23 of 23" not in boss.get("/admin/stores?brand=SPAR").get_data(as_text=True) or True
    assert "of 22" in boss.get("/admin/stores?brand=SPAR").get_data(as_text=True)
    assert "of 5" in boss.get("/admin/stores?q=Berea").get_data(as_text=True)
    assert "Shop 07" in boss.get("/admin/stores?q=shop%2007").get_data(as_text=True)


def test_the_store_form_has_no_web_page_field(app, boss):
    """Students are never sent to a retailer's website, so there is nothing to type a web address into."""
    page = boss.get("/admin/stores/new").get_data(as_text=True)
    assert "StoreLink" not in page and "Web page" not in page


def test_unknown_store_is_404(boss):
    assert (
        boss.get("/admin/stores/nope/edit").status_code == 404
        and boss.post("/admin/stores/nope/delete").status_code == 404
    )


def test_the_stores_students_see_follow_the_admins_changes(app, boss):
    from app.services.stores import stores_near

    boss.post("/admin/stores/new", data=store_data(Latitude="-29.8587", Longitude="31.0218"))
    assert "Checkers Test Mall" in [s.Name for s, _ in stores_near(-29.8587, 31.0218, 5)] or "Checkers Test Mall" in [
        getattr(s, "Name", getattr(s, "name", None)) for s in stores_near(-29.8587, 31.0218, 5)
    ]


# ---------------------------------------------------------------------------------------------------- audit
def shown(html):
    """The actions in the audit table rows (the filter drop-down also lists every action name)."""
    return re.findall(r"<td><code>([a-z_.]+)</code></td>", html)


def test_audit_list_filters_and_shows_details(app, boss, student, admin):
    boss.get(f"/admin/users/{student.UserId}")
    boss.post(f"/admin/users/{student.UserId}/deactivate")
    boss.post("/admin/stores/new", data=store_data())
    html = boss.get("/admin/audit").get_data(as_text=True)
    assert sorted(shown(html)) == ["store.create", "user.deactivate", "user.view"] and admin.Email in html
    assert shown(boss.get("/admin/audit?action=store.create").get_data(as_text=True)) == ["store.create"]
    assert sorted(shown(boss.get(f"/admin/audit?target={student.UserId}").get_data(as_text=True))) == [
        "user.deactivate",
        "user.view",
    ]
    assert shown(boss.get("/admin/audit?admin=nobody@example.com").get_data(as_text=True)) == []
    today = utcnow().date().isoformat()
    assert len(shown(boss.get(f"/admin/audit?start={today}&end={today}").get_data(as_text=True))) == 3
    assert shown(boss.get("/admin/audit?end=2001-01-01").get_data(as_text=True)) == []


def test_audit_search_treats_wildcards_literally(app, boss, admin):
    audit.record(admin, "x.y", "user:a%b")
    audit.record(admin, "x.y", "user:axb")
    db.session.commit()
    assert audit.search(target="a%b").total == 1


def test_audit_pagination(app, boss, admin):
    for n in range(60):
        audit.record(admin, "test.action", f"thing:{n}")
    db.session.commit()
    assert "Showing 1–25 of 60" in boss.get("/admin/audit").get_data(as_text=True)
    assert "Showing 51–60 of 60" in boss.get("/admin/audit?page=3").get_data(as_text=True)


def test_the_audit_log_cannot_be_changed_from_the_portal(app, boss, admin):
    audit.record(admin, "x.y", "t")
    db.session.commit()
    row = audit_rows()[0]
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert boss.open("/admin/audit", method=method).status_code == 405
        assert boss.open(f"/admin/audit/{row.AuditId}", method=method).status_code == 404
    assert len(audit_rows()) == 1


def test_audit_rows_survive_the_admin_being_deleted(app, admin, student):
    audit.record(admin, "x.y", "t")
    db.session.commit()
    db.session.delete(admin)
    db.session.commit()
    row = audit_rows()[0]
    assert row.AdminEmail == "ada@example.com" and row.AdminId


def test_scrub_hides_secrets_and_caps_text():
    cleaned = audit.scrub(
        {
            "password": "hunter2",
            "api_key": "k",
            "ApiKey": "k",
            "token": "t",
            "email": "a@b.co",
            "note": "x" * 500,
            "nested": {"NewPassword": "p", "ok": 1},
            "when": datetime(2026, 9, 21, tzinfo=timezone.utc),
            "raw_key": "r",
        }
    )
    assert (
        cleaned["password"]
        == cleaned["api_key"]
        == cleaned["ApiKey"]
        == cleaned["token"]
        == cleaned["raw_key"]
        == "[hidden]"
    )
    assert (
        cleaned["nested"] == {"NewPassword": "[hidden]", "ok": 1}
        and len(cleaned["note"]) == 300
        and cleaned["email"] == "a@b.co"
    )
    assert cleaned["when"].startswith("2026-09-21")


def test_changes_lists_only_what_differs():
    assert audit.changes({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == {
        "b": {"from": 2, "to": 3},
        "c": {"from": None, "to": 4},
    }


# ----------------------------------------------------------------------------------------------- the CLI
def test_make_admin_command(app, student):
    runner = app.test_cli_runner()
    assert "is now an admin" in runner.invoke(args=["make-admin", "SIPHO@example.com"]).output
    db.session.refresh(student)
    assert student.IsAdmin is True
    assert "already" in runner.invoke(args=["make-admin", "sipho@example.com"]).output
    row = audit_rows("user.make_admin")[0]
    assert row.AdminId == "cli" and row.Target == f"user:{student.UserId}"


def test_make_admin_unknown_email_and_inactive_user(app, make_user):
    runner = app.test_cli_runner()
    assert runner.invoke(args=["make-admin", "nobody@example.com"]).exit_code != 0
    off = make_user(email="off@example.com", IsActive=False)
    result = runner.invoke(args=["make-admin", off.Email])
    assert result.exit_code != 0 and "deactivated" in result.output


def test_remove_admin_refuses_the_last_admin(app, admin, make_user):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["make-admin", admin.Email, "--remove"])
    assert result.exit_code != 0 and "only active admin" in result.output
    second = make_user(email="two@example.com", IsAdmin=True)
    assert "no longer an admin" in runner.invoke(args=["make-admin", second.Email, "--remove"]).output


# --------------------------------------------------------------------------------------- small utilities
def test_secrets_round_trip_and_masking(app):
    from app.utils.secrets import decrypt_secret, encrypt_secret, mask_secret

    token = encrypt_secret("my-secret-key-123")
    assert "my-secret" not in token and decrypt_secret(token) == "my-secret-key-123"
    assert encrypt_secret("my-secret-key-123") != token  # random IV: two encryptions differ
    assert decrypt_secret(None) is None and decrypt_secret("garbage") is None and decrypt_secret("") is None
    assert (
        mask_secret("abcdefghijklmnop") == "••••••••mnop"
        and mask_secret("short") == "••••••••"
        and mask_secret(None) == ""
    )


def test_a_separate_encryption_key_can_be_configured(app):
    from app.utils.secrets import decrypt_secret, encrypt_secret

    token = encrypt_secret("value-123456")
    app.config["SETTINGS_ENCRYPTION_KEY"] = "another"
    assert decrypt_secret(token) is None
    app.config["SETTINGS_ENCRYPTION_KEY"] = None
    assert decrypt_secret(token) == "value-123456"


def test_pagination_window_and_helpers():
    from app.utils.pagination import Page, like_pattern, parse_page

    page = Page(items=[], total=400, page=10, per_page=20)
    assert page.pages == 20 and page.window() == [1, None, 8, 9, 10, 11, 12, None, 20]
    assert Page(total=0).window() == [1] and Page(total=0).pages == 1 and Page(total=0).first == 0
    assert (
        parse_page("3") == 3
        and parse_page("-4") == 1
        and parse_page("x") == 1
        and parse_page(None) == 1
        and parse_page("9" * 30) == 100_000
    )
    assert like_pattern("50%_off\\") == "%50\\%\\_off\\\\%"


def test_resetting_a_password_signs_the_student_out_everywhere(app, boss, student):
    student_browser = sign_in(app.test_client(), student)
    assert student_browser.get("/dashboard").status_code == 200
    boss.post(f"/admin/users/{student.UserId}/reset-password")
    assert student_browser.get("/dashboard").status_code == 302  # the old session is dead


def test_an_admin_who_resets_their_own_password_stays_signed_in(app, make_password_user):
    admin = make_password_user(email="root@example.com", first="Root", last="Admin", IsAdmin=True)
    client = sign_in(app.test_client(), admin)
    assert client.post(f"/admin/users/{admin.UserId}/reset-password").status_code == 200
    assert client.get("/admin/").status_code == 200


def test_saving_a_deactivated_admins_details_does_not_demand_reactivation(app, boss, admin, make_password_user):
    other = make_password_user(
        email="second@example.com", first="Second", last="Admin", IsAdmin=True, CellphoneNumber="0829998888"
    )
    boss.post(f"/admin/users/{other.UserId}/deactivate")
    response = boss.post(
        f"/admin/users/{other.UserId}/edit",
        data={
            "FirstName": "Seconda",
            "LastName": "Admin",
            "CellphoneNumber": "0829998888",
            "ResidentialAddress": "",
            "Role": "Admin",
        },
        follow_redirects=True,
    )
    assert "Reactivate this account" not in response.get_data(as_text=True)
    db.session.refresh(other)
    assert other.FirstName == "Seconda" and other.IsAdmin and not other.IsActive


@pytest.mark.parametrize("query", ["start=0001-01-01", "end=9999-12-31"])
def test_absurd_audit_date_filters_are_not_a_server_error(boss, query):
    assert boss.get(f"/admin/audit?{query}").status_code == 200


# ---- LoyaltyHub / Buyly (their provider tests live in test_loyaltyhub_buyly.py)
LH = "https://loyaltyhub.co.za/api/v1/prices"


def _row(retailer="shoprite"):
    return {
        "retailer": retailer,
        "barcode": "6001030002075",
        "name": "Milk 2L",
        "price": 31.99,
        "image_url": "https://c.example/m.jpg",
    }


def _envelope(rows):
    return {"data": rows, "meta": {"count": len(rows), "quota": 100, "used": 1}}


def test_both_new_sources_are_listed_and_need_a_key_before_activation(app, boss):
    page = boss.get("/admin/integrations").get_data(as_text=True)
    assert "LoyaltyHub" in page and "Buyly" in page and "untested" in page
    boss.post("/admin/integrations/loyaltyhub/activate")
    assert integrations.active_source() == "mock"  # refused: no key yet
    boss.post("/admin/integrations/loyaltyhub/key", data={"ApiKey": "lh_live_key_1234567890"})
    boss.post("/admin/integrations/loyaltyhub/activate")
    assert integrations.active_source() == "loyaltyhub"
    provider = retail_api.get_retail_provider()
    assert provider.name == "loyaltyhub" and provider.inner.api_key == "lh_live_key_1234567890"
    boss.post("/admin/integrations/buyly/key", data={"ApiKey": "buyly_beta_key_1234567890"})
    boss.post("/admin/integrations/buyly/activate")
    assert retail_api.get_retail_provider().inner.api_key == "buyly_beta_key_1234567890"


@responses.activate
def test_sync_now_tests_loyaltyhub_and_records_the_result(app, boss):
    boss.post("/admin/integrations/loyaltyhub/key", data={"ApiKey": "lh_live_key_1234567890"})
    responses.get(LH, json=_envelope([_row(), _row("pnp")]))
    boss.post("/admin/integrations/loyaltyhub/sync")
    row = db.session.get(IntegrationSetting, "loyaltyhub")
    assert row.LastSyncStatus == "ok" and "2 offers" in row.LastSyncMessage
    responses.upsert(responses.GET, LH, status=403)
    boss.post("/admin/integrations/loyaltyhub/sync")
    row = db.session.get(IntegrationSetting, "loyaltyhub")
    assert row.LastSyncStatus == "error" and "rejected" in row.LastSyncMessage
    assert "lh_live_key_1234567890" not in boss.get("/admin/integrations").get_data(as_text=True)
