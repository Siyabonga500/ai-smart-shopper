import io
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import responses
from sqlalchemy import select

from app.extensions import db
from app.models import Budget, ListItem, Preference, ShoppingList, User
from app.services.account_stats import account_stats

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64
SEARCH = "https://nominatim.openstreetmap.org/search"


@pytest.fixture
def uploads(app, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    return tmp_path


@pytest.fixture
def me(client, make_password_user):
    user = make_password_user(
        email="me@example.com",
        CellphoneNumber="0821234567",
        ResidentialAddress="1 Old Street, Durban",
        SAIdLast4="9087",
        Latitude=-29.86,
        Longitude=31.02,
    )
    client.post("/login", data={"Email": "me@example.com", "Password": "Str0ng!Pass"})
    return user


def _edit(client, **overrides):
    data = {
        "FirstName": "Sipho",
        "LastName": "Dlamini",
        "CellphoneNumber": "0821234567",
        "ResidentialAddress": "1 Old Street, Durban",
        "Latitude": "-29.860000",
        "Longitude": "31.020000",
    }
    data.update(overrides)
    return client.post("/profile/edit", data=data, content_type="multipart/form-data")


# ------------------------------------------------------------------------------ view
def test_profile_shows_details_with_masked_id(client, me):
    html = client.get("/profile").get_data(as_text=True)
    assert "me@example.com" in html and "0821234567" in html and "1 Old Street, Durban" in html
    assert "*********9087" in html
    assert "Member since" in html and "Last sign-in" in html
    assert "SD" in html  # initials avatar when there is no photo


def test_profile_trailing_slash_still_works(client, me):
    assert client.get("/profile/").status_code == 200


# ------------------------------------------------------------------------------ edit
def test_edit_updates_details_but_never_the_email(client, me):
    response = _edit(
        client, FirstName="Sipho", LastName="Zulu", CellphoneNumber="0731112222", Email="hacker@evil.example"
    )
    assert response.status_code == 302
    user = db.session.get(User, me.UserId)
    assert (user.LastName, user.CellphoneNumber, user.Email) == ("Zulu", "0731112222", "me@example.com")


@pytest.mark.parametrize("field, value", [("FirstName", "S"), ("CellphoneNumber", "12345"), ("ResidentialAddress", "")])
def test_edit_validates(client, me, field, value):
    response = _edit(client, **{field: value})
    assert response.status_code == 400
    assert db.session.get(User, me.UserId).FirstName == "Sipho"


def test_new_pin_is_saved(client, me):
    _edit(client, ResidentialAddress="Campbell Hall, Glenwood", Latitude="-29.8543", Longitude="30.9977")
    user = db.session.get(User, me.UserId)
    assert (user.Latitude, user.Longitude) == (pytest.approx(-29.8543), pytest.approx(30.9977))


def test_unchanged_address_keeps_the_cached_position(client, me):
    _edit(client, Latitude="", Longitude="")  # same address, no pin posted
    user = db.session.get(User, me.UserId)
    assert user.Latitude == pytest.approx(-29.86)


@responses.activate
def test_changed_address_without_a_pin_is_geocoded(app, client, me):
    app.config["GEOCODE_ON_SAVE"] = True
    responses.get(SEARCH, json=[{"display_name": "x", "lat": "-29.8517", "lon": "31.0003"}])
    _edit(client, ResidentialAddress="79 Mansfield Road, Berea", Latitude="", Longitude="")
    user = db.session.get(User, me.UserId)
    assert (user.Latitude, user.Longitude) == (pytest.approx(-29.8517), pytest.approx(31.0003))


@responses.activate
def test_stale_pin_next_to_an_edited_address_is_not_trusted(app, client, me):
    """JavaScript off: the browser posts the OLD hidden pin back with the NEW address text."""
    app.config["GEOCODE_ON_SAVE"] = True
    responses.get(SEARCH, json=[{"display_name": "x", "lat": "-29.7", "lon": "31.1"}])
    _edit(client, ResidentialAddress="Somewhere Else, Umhlanga")  # hidden pin stays -29.86 / 31.02
    user = db.session.get(User, me.UserId)
    assert user.Latitude == pytest.approx(-29.7)


@responses.activate
def test_failed_geocoding_clears_the_old_pin_instead_of_keeping_a_wrong_one(app, client, me):
    app.config["GEOCODE_ON_SAVE"] = True
    responses.get(SEARCH, status=500)
    _edit(client, ResidentialAddress="Nowhere Road", Latitude="", Longitude="")
    user = db.session.get(User, me.UserId)
    assert user.ResidentialAddress == "Nowhere Road" and user.Latitude is None


# ------------------------------------------------------------------------------ photo
@pytest.mark.parametrize("content, ext", [(PNG, "png"), (JPEG, "jpg"), (WEBP, "webp")])
def test_photo_upload_saves_a_randomly_named_file(client, me, uploads, content, ext):
    response = _edit(client, ProfilePic=(io.BytesIO(content), "../../evil name.jpg"))
    assert response.status_code == 302
    user = db.session.get(User, me.UserId)
    assert user.ProfilePic.startswith("uploads/profile/") and user.ProfilePic.endswith("." + ext)
    assert "evil" not in user.ProfilePic
    assert (uploads / "profile" / user.ProfilePic.rsplit("/", 1)[1]).read_bytes() == content
    assert user.ProfilePic in client.get("/profile").get_data(as_text=True)


def test_photo_must_really_be_an_image(client, me, uploads):
    response = _edit(client, ProfilePic=(io.BytesIO(b"<?php echo 1; ?>"), "shell.png"))
    assert response.status_code == 400
    assert "not a JPG, PNG or WEBP" in response.get_data(as_text=True)
    assert db.session.get(User, me.UserId).ProfilePic is None
    assert not list((uploads / "profile").glob("*")) if (uploads / "profile").exists() else True


def test_wrong_extension_is_refused_before_the_content_is_read(client, me, uploads):
    response = _edit(client, ProfilePic=(io.BytesIO(PNG), "notes.gif"))
    assert response.status_code == 400 and "Choose a JPG, PNG or WEBP" in response.get_data(as_text=True)


def test_svg_is_refused(client, me, uploads):
    assert _edit(client, ProfilePic=(io.BytesIO(b"<svg onload=alert(1)>"), "x.svg")).status_code == 400


def test_photo_over_2mb_is_refused(client, me, uploads):
    big = PNG + b"\x00" * (2 * 1024 * 1024)
    response = _edit(client, ProfilePic=(io.BytesIO(big), "big.png"))
    assert response.status_code == 400 and "2MB" in response.get_data(as_text=True)


def test_request_far_over_the_limit_gets_413(client, me, uploads):
    huge = PNG + b"\x00" * (3 * 1024 * 1024)
    assert _edit(client, ProfilePic=(io.BytesIO(huge), "huge.png")).status_code == 413


def test_replacing_or_removing_a_photo_deletes_the_old_file(client, me, uploads):
    _edit(client, ProfilePic=(io.BytesIO(PNG), "a.png"))
    first = db.session.get(User, me.UserId).ProfilePic
    first_file = uploads / "profile" / first.rsplit("/", 1)[1]
    assert first_file.exists()

    _edit(client, ProfilePic=(io.BytesIO(JPEG), "b.jpg"))
    second = db.session.get(User, me.UserId).ProfilePic
    assert second != first and not first_file.exists()

    _edit(client, RemovePhoto="y")
    assert db.session.get(User, me.UserId).ProfilePic is None
    assert not list((uploads / "profile").glob("*"))


def test_delete_ignores_paths_that_are_not_ours(app, uploads):
    from app.services.photos import delete_profile_photo

    outside = uploads / "secret.txt"
    outside.write_text("keep")
    delete_profile_photo("uploads/profile/../secret.txt")  # traversal is reduced to a bare file name
    delete_profile_photo("/etc/passwd")
    delete_profile_photo(None)
    assert outside.exists()


# ------------------------------------------------------------------------------ password
def _password(client, current="Str0ng!Pass", new="N3w!Password", confirm=None):
    return client.post(
        "/profile/password",
        data={"pw-CurrentPassword": current, "pw-NewPassword": new, "pw-ConfirmPassword": confirm or new},
    )


def test_change_password(client, me):
    assert _password(client).status_code == 302
    assert db.session.get(User, me.UserId).check_password("N3w!Password")
    client.get("/logout")
    assert client.post("/login", data={"Email": "me@example.com", "Password": "N3w!Password"}).status_code == 302


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"current": "wrong"}, "current password is incorrect"),
        ({"new": "weak"}, "Your password needs"),
        ({"confirm": "Different1!"}, "do not match"),
        ({"new": "Str0ng!Pass"}, "not used just now"),
    ],
)
def test_change_password_rejections(client, me, kwargs, message):
    response = _password(client, **kwargs)
    assert response.status_code == 400 and message in response.get_data(as_text=True)
    assert db.session.get(User, me.UserId).check_password("Str0ng!Pass")


def test_microsoft_only_account_can_set_a_first_password_without_a_current_one(client, make_user, login):
    user = make_user()
    login(user)
    response = client.post(
        "/profile/password", data={"pw-NewPassword": "N3w!Password", "pw-ConfirmPassword": "N3w!Password"}
    )
    assert response.status_code == 302
    assert db.session.get(User, user.UserId).check_password("N3w!Password")


def test_password_guessing_via_the_change_form_is_throttled(app, client, me):
    from app.extensions import login_limiter

    app.config["RATELIMIT_ENABLED"] = True
    login_limiter.clear()
    for _ in range(app.config["LOGIN_FAILURE_LIMIT"]):
        _password(client, current="nope")
    assert "Too many attempts" in _password(client).get_data(as_text=True)
    assert db.session.get(User, me.UserId).check_password("Str0ng!Pass")
    login_limiter.clear()


# ------------------------------------------------------------------------------ preferences
def test_add_and_remove_preferences(client, me):
    assert (
        client.post(
            "/profile/preferences", data={"PreferenceType": "Dietary", "PreferenceValue": "Vegetarian"}
        ).status_code
        == 302
    )
    client.post("/profile/preferences", data={"PreferenceType": "Store", "PreferenceValue": "Checkers"})
    html = client.get("/profile").get_data(as_text=True)
    assert "Vegetarian" in html and "Checkers" in html

    pref = db.session.scalars(select(Preference).where(Preference.PreferenceType == "Dietary")).one()
    assert pref.UserId == me.UserId and pref.PreferenceId.startswith("PRF-")
    assert client.post(f"/profile/preferences/{pref.PreferenceId}/delete").status_code == 302
    assert db.session.scalars(select(Preference)).all()[0].PreferenceType == "Store"


def test_profile_shows_total_saved_preferences(client, me):
    client.post("/profile/preferences", data={"PreferenceType": "Brand", "PreferenceValue": "Albany"})
    client.post("/profile/preferences", data={"PreferenceType": "Store", "PreferenceValue": "Checkers"})
    html = client.get("/profile").get_data(as_text=True)
    assert "2 saved" in html and "personalise recommendations" in html


def test_preference_validation_and_duplicates(client, me):
    assert (
        client.post("/profile/preferences", data={"PreferenceType": "Colour", "PreferenceValue": "Red"}).status_code
        == 400
    )
    assert (
        client.post("/profile/preferences", data={"PreferenceType": "Brand", "PreferenceValue": ""}).status_code == 400
    )
    client.post("/profile/preferences", data={"PreferenceType": "Brand", "PreferenceValue": "Albany"})
    dup = client.post("/profile/preferences", data={"PreferenceType": "Brand", "PreferenceValue": "albany"})
    assert dup.status_code == 400 and "already in your Brand" in dup.get_data(as_text=True)
    assert len(db.session.scalars(select(Preference)).all()) == 1


def test_preference_limit(app, client, me):
    app.config["MAX_PREFERENCES_PER_USER"] = 2
    for value in ("A1", "B2", "C3"):
        client.post("/profile/preferences", data={"PreferenceType": "Brand", "PreferenceValue": value})
    assert len(db.session.scalars(select(Preference)).all()) == 2


def test_cannot_delete_someone_elses_preference(client, me, make_user):
    other = make_user()
    pref = Preference(UserId=other.UserId, PreferenceType="Brand", PreferenceValue="Clover")
    db.session.add(pref)
    db.session.commit()
    client.post(f"/profile/preferences/{pref.PreferenceId}/delete")
    assert db.session.get(Preference, pref.PreferenceId) is not None


# ------------------------------------------------------------------------------ stats
def test_account_stats(me, make_user):
    other = make_user()
    b1 = Budget(
        UserId=me.UserId, Title="Aug", TotalAmount=Decimal("1750"), UsedAmount=Decimal("1200.50"), IsActive=False
    )
    b2 = Budget(UserId=me.UserId, Title="Sep", TotalAmount=Decimal("1750"), UsedAmount=Decimal("300"), IsActive=True)
    foreign = Budget(UserId=other.UserId, Title="Not mine", TotalAmount=1, UsedAmount=Decimal("999"), IsActive=True)
    db.session.add_all([b1, b2, foreign])
    db.session.flush()
    closed = ShoppingList(
        UserId=me.UserId,
        BudgetId=b1.BudgetId,
        Title="Done",
        IsActive=False,
        DateClosed=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )
    open_list = ShoppingList(UserId=me.UserId, BudgetId=b2.BudgetId, Title="Open", IsActive=True)
    db.session.add_all([closed, open_list])
    db.session.flush()
    for n in range(7):
        db.session.add(
            ListItem(
                UserId=me.UserId,
                ShoppingListId=closed.ShoppingListId,
                ItemName=f"Bought {n}",
                UnitCost=Decimal("10"),
                ItemQuantity=1,
                IsPurchased=True,
                PurchasedDate=datetime(2026, 8, 1 + n, tzinfo=timezone.utc),
                DateCreated=datetime(2026, 8, 1 + n, tzinfo=timezone.utc),
            )
        )
    db.session.add(
        ListItem(
            UserId=me.UserId,
            ShoppingListId=open_list.ShoppingListId,
            ItemName="Still on the list",
            UnitCost=Decimal("5"),
            ItemQuantity=1,
        )
    )
    db.session.commit()

    stats = account_stats(me.UserId)
    assert stats.budget_count == 2
    assert stats.active_budget.Title == "Sep"
    assert stats.total_spent == Decimal("70.00")  # only what was bought; the open list and budget totals do not count
    assert [i.ItemName for i in stats.recent_purchases] == ["Bought 6", "Bought 5", "Bought 4", "Bought 3", "Bought 2"]


def test_stats_for_a_brand_new_account(me):
    stats = account_stats(me.UserId)
    assert (stats.budget_count, stats.active_budget, stats.total_spent, stats.recent_purchases) == (
        0,
        None,
        Decimal("0.00"),
        [],
    )


def test_profile_page_shows_stats_and_zar(client, me):
    budget = Budget(
        UserId=me.UserId, Title="Sep", TotalAmount=Decimal("1750"), UsedAmount=Decimal("1050"), IsActive=False
    )
    db.session.add(budget)
    db.session.flush()
    trip = ShoppingList(
        UserId=me.UserId,
        BudgetId=budget.BudgetId,
        Title="Trip",
        IsActive=False,
        DateClosed=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    db.session.add(trip)
    db.session.flush()
    db.session.add(
        ListItem(
            UserId=me.UserId,
            ShoppingListId=trip.ShoppingListId,
            ItemName="Fridge",
            UnitCost=Decimal("1050"),
            ItemQuantity=1,
            IsPurchased=True,
            PurchasedDate=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
    )
    db.session.commit()
    html = client.get("/profile").get_data(as_text=True)
    assert "R1 050" in html.replace("\xa0", " ") and "Sep" in html
    assert "Fridge" in html


@pytest.mark.parametrize("number", ["0311234567", "0921234567", "082123456", "+27821234567", "082123456a"])
def test_profile_shows_the_phone_rule_inline_and_saves_nothing(client, me, number):
    response = _edit(client, CellphoneNumber=number)
    assert response.status_code == 400
    assert "Enter your cellphone number as 10 digits, for example 0821234567." in response.get_data(as_text=True)
    assert db.session.get(User, me.UserId).CellphoneNumber == "0821234567"
