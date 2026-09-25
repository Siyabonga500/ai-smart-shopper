from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app import create_app
from app.config import TestConfig
from app.data.dut_residences import DUT_RESIDENCES
from app.extensions import db, login_limiter
from app.models import User
from app.routes.auth import _on_microsoft_authorized, complete_microsoft_login


def _register(client, data, **kwargs):
    return client.post("/register", data=data, **kwargs)


def _only_user():
    return db.session.scalars(select(User)).one()


# ------------------------------------------------------------------------------ register page
def test_register_page_renders_every_field_and_all_dut_residences(client):
    html = client.get("/register").get_data(as_text=True)
    for name in (
        "FirstName",
        "LastName",
        "Email",
        "CellphoneNumber",
        "SAIdNumber",
        "Gender",
        "Race",
        "Residence",
        "ResidentialAddress",
        "Password",
        "ConfirmPassword",
        "AcceptTerms",
        "Latitude",
        "Longitude",
    ):
        assert f'name="{name}"' in html
    for residence in DUT_RESIDENCES:
        assert residence.name in html
    assert len(DUT_RESIDENCES) >= 30
    assert all(residence.latitude is not None and residence.longitude is not None for residence in DUT_RESIDENCES)
    assert "Other / private accommodation" in html
    for option in ("Male", "Female", "Other", "Prefer not to say", "African", "Coloured", "Indian/Asian", "White"):
        assert f">{option}</option>" in html
    assert "map_picker.js" in html and "leaflet" in html.lower()


def test_register_page_is_readable_on_the_registered_csrf_free_test_config(client):
    assert client.get("/register").status_code == 200


# ------------------------------------------------------------------------------ registering
def test_successful_registration_creates_user_and_signs_them_in(client, registration_data):
    response = _register(client, registration_data)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")

    user = _only_user()
    assert (user.FirstName, user.LastName, user.Email) == ("Thandi", "Nkosi", "22226534@dut4life.ac.za")
    assert user.CellphoneNumber == "0821234567"
    assert user.DateOfBirth == date(2002, 3, 15)
    assert user.Gender == "Female"  # derived from the ID because the field was blank
    assert user.Race == "African"
    assert user.CreatedOn is not None and user.LastLogin is not None
    assert user.TermsAcceptedOn is not None
    assert user.MicrosoftId is None
    assert client.get("/dashboard").status_code == 200  # signed in


def test_password_is_stored_as_a_bcrypt_hash(client, registration_data):
    _register(client, registration_data)
    user = _only_user()
    assert user.PasswordHash.startswith("$2b$")
    assert "Str0ng!Pass" not in user.PasswordHash
    assert user.check_password("Str0ng!Pass") and not user.check_password("wrong")


def test_full_sa_id_is_never_stored(client, registration_data, app):
    _register(client, registration_data)
    user = _only_user()
    number = registration_data["SAIdNumber"]
    assert user.SAIdLast4 == number[-4:]
    assert user.masked_sa_id == "*********" + number[-4:]
    raw = " ".join(str(getattr(user, column.name)) for column in User.__table__.columns)
    assert number not in raw


def test_residence_fills_the_address_when_left_blank(client, registration_data):
    _register(client, registration_data)
    assert _only_user().ResidentialAddress == "Campbell Hall, 23 James Henderson Crescent, Glenwood, Durban 4001"


def test_typed_address_is_kept_and_pin_is_saved(client, registration_data):
    registration_data.update(
        Residence="other", ResidentialAddress="12 Musgrave Road, Berea", Latitude="-29.8499", Longitude="31.0084"
    )
    _register(client, registration_data)
    user = _only_user()
    assert user.ResidentialAddress == "12 Musgrave Road, Berea"
    assert (user.Latitude, user.Longitude) == (pytest.approx(-29.8499), pytest.approx(31.0084))


def test_editable_gender_wins_over_the_id(client, registration_data):
    registration_data["Gender"] = "Prefer not to say"
    _register(client, registration_data)
    assert _only_user().Gender == "Prefer not to say"


def test_email_is_stored_lower_case(client, registration_data):
    registration_data["Email"] = "  22226534@DUT4Life.AC.ZA "
    _register(client, registration_data)
    assert _only_user().Email == "22226534@dut4life.ac.za"


def test_out_of_range_pin_is_ignored_not_stored(client, registration_data):
    registration_data.update(Latitude="999", Longitude="31")
    assert _register(client, registration_data).status_code == 302
    assert _only_user().Latitude is None


# ------------------------------------------------------------------------------ validation
@pytest.mark.parametrize(
    "field, value, message",
    [
        ("FirstName", "A", "between 2 and 50"),
        ("FirstName", "A" * 51, "between 2 and 50"),
        ("FirstName", "Th4ndi", "can only contain letters"),
        ("LastName", "", "Enter your last name"),
        ("Email", "not-an-email", "valid email"),
        ("CellphoneNumber", "082123", "10 digits"),
        ("CellphoneNumber", "08212345678", "10 digits"),
        ("SAIdNumber", "123", "13 digits"),
        ("SAIdNumber", "12345678901234", "13 digits"),
        ("SAIdNumber", "12345abc90123", "13 digits"),
        ("Email", "thandi@example.com", "DUT student email"),
        ("Email", "thandi@dut4life.ac.za.evil.com", "DUT student email"),
        ("Race", "", "Select an option"),
        ("Password", "weak", "Your password needs"),
        ("ConfirmPassword", "Different1!", "do not match"),
        ("AcceptTerms", "", "accept the Terms"),
    ],
)
def test_server_side_validation_rejects_bad_input(client, registration_data, field, value, message):
    registration_data[field] = value
    if not value:
        registration_data.pop(field)
    response = _register(client, registration_data)
    assert response.status_code == 200
    assert message in response.get_data(as_text=True)
    assert db.session.scalars(select(User)).all() == []


@pytest.mark.parametrize("name", ["Nomvula", "Anne-Marie", "O'Brien", "Zoë", "de Klerk", "N’Dour"])
def test_names_with_hyphens_apostrophes_and_accents_are_fine(client, registration_data, name):
    registration_data["FirstName"] = name
    assert _register(client, registration_data).status_code == 302


def test_address_is_required_when_no_residence_is_chosen(client, registration_data):
    registration_data.update(Residence="other", ResidentialAddress="")
    response = _register(client, registration_data)
    assert response.status_code == 200
    assert "Enter your residential address" in response.get_data(as_text=True)


def test_unknown_residence_and_race_values_are_rejected(client, registration_data):
    for field, value in (("Residence", "hacker-hall"), ("Race", "Martian"), ("Gender", "Robot")):
        data = dict(registration_data, **{field: value})
        assert _register(client, data).status_code == 200


def test_duplicate_email_is_refused_case_insensitively(client, registration_data, make_password_user):
    make_password_user(email="22226534@DUT4LIFE.ac.za")
    response = _register(client, registration_data)
    assert response.status_code == 200
    assert "already exists" in response.get_data(as_text=True)


def test_same_sa_id_cannot_register_twice(client, registration_data):
    _register(client, registration_data)
    client.get("/logout")
    second = dict(registration_data, Email="other@example.com")
    response = _register(client, second)
    assert response.status_code == 200
    assert "ID number already exists" in response.get_data(as_text=True)
    assert len(db.session.scalars(select(User)).all()) == 1


def test_signed_in_users_are_redirected_away_from_register(client, make_user, login):
    login(make_user())
    assert client.get("/register").status_code == 302


# ------------------------------------------------------------------------------ login / logout
def test_login_with_correct_password(client, make_password_user):
    user = make_password_user()
    before = user.LastLogin
    response = client.post("/login", data={"Email": "Student@Example.com", "Password": "Str0ng!Pass"})
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200
    assert db.session.get(User, user.UserId).LastLogin != before or before is None


@pytest.mark.parametrize(
    "email, password",
    [
        ("student@example.com", "wrong"),
        ("nobody@example.com", "Str0ng!Pass"),
        ("student@example.com", "str0ng!pass"),
    ],
)
def test_login_failures_share_one_message(client, make_password_user, email, password):
    make_password_user()
    response = client.post("/login", data={"Email": email, "Password": password})
    assert response.status_code == 401
    assert "Incorrect email or password." in response.get_data(as_text=True)
    assert client.get("/dashboard").status_code == 302


def test_microsoft_only_account_cannot_sign_in_with_a_blank_or_any_password(client, make_user):
    make_user(email="ms@example.com")
    assert client.post("/login", data={"Email": "ms@example.com", "Password": "anything"}).status_code == 401


def test_login_redirects_to_next_only_when_it_is_local(client, make_password_user):
    make_password_user()
    good = client.post("/login?next=/budget/", data={"Email": "student@example.com", "Password": "Str0ng!Pass"})
    assert good.headers["Location"].endswith("/budget/")
    client.get("/logout")
    for evil in ("https://evil.example/steal", "//evil.example", "/\\evil.example"):
        response = client.post(
            "/login", query_string={"next": evil}, data={"Email": "student@example.com", "Password": "Str0ng!Pass"}
        )
        assert response.headers["Location"].endswith("/dashboard"), evil
        client.get("/logout")


def test_protected_page_bounces_to_login_and_back(client, make_password_user):
    make_password_user()
    redirect = client.get("/budget/")
    assert "/login?next=" in redirect.headers["Location"]
    response = client.post(
        redirect.headers["Location"], data={"Email": "student@example.com", "Password": "Str0ng!Pass"}
    )
    assert response.headers["Location"].endswith("/budget/")


def test_login_is_throttled_after_repeated_failures(app, client, make_password_user):
    app.config["RATELIMIT_ENABLED"] = True
    make_password_user()
    login_limiter.clear()
    for _ in range(app.config["LOGIN_FAILURE_LIMIT"]):
        assert client.post("/login", data={"Email": "student@example.com", "Password": "bad"}).status_code == 401
    blocked = client.post("/login", data={"Email": "student@example.com", "Password": "Str0ng!Pass"})
    assert blocked.status_code == 429  # even the right password waits
    login_limiter.clear()
    assert client.post("/login", data={"Email": "student@example.com", "Password": "Str0ng!Pass"}).status_code == 302


def test_remember_me_sets_a_cookie(client, make_password_user):
    make_password_user()
    response = client.post("/login", data={"Email": "student@example.com", "Password": "Str0ng!Pass", "Remember": "y"})
    assert any("remember_token" in header for header in response.headers.getlist("Set-Cookie"))


def test_logout_clears_the_session(client, make_password_user):
    make_password_user()
    client.post("/login", data={"Email": "student@example.com", "Password": "Str0ng!Pass"})
    assert client.get("/logout").status_code == 302
    assert client.get("/dashboard").status_code == 302


def test_api_paths_answer_401_json_not_a_redirect(client):
    response = client.get("/api/stores/near")
    assert response.status_code == 401
    assert response.get_json()["error"]


# ------------------------------------------------------------------------------ Microsoft
def test_microsoft_route_explains_when_not_configured(client):
    response = client.get("/auth/microsoft", follow_redirects=True)
    assert b"not set up" in response.data


@pytest.fixture
def microsoft_app(monkeypatch):
    monkeypatch.setenv("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    monkeypatch.setattr(TestConfig, "MICROSOFT_CLIENT_ID", "client-id")
    monkeypatch.setattr(TestConfig, "MICROSOFT_CLIENT_SECRET", "client-secret")
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_microsoft_button_starts_the_flow(microsoft_app):
    response = microsoft_app.test_client().get("/auth/microsoft")
    assert response.status_code == 302 and "/auth/azure" in response.headers["Location"]


def _add_user(email, password=None, microsoft_id=None):
    user = User(FirstName="Old", LastName="Timer", Email=email, MicrosoftId=microsoft_id)
    if password:
        user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def test_new_microsoft_user_completes_registration_without_a_password(microsoft_app, registration_data):
    from flask import session

    client = microsoft_app.test_client()
    # Simulate flask-dance calling our receiver with a token, and Graph answering /me.
    blueprint = MagicMock()
    blueprint.session.get.return_value = MagicMock(
        ok=True, json=lambda: {"id": "ms-123", "mail": "Sipho@dut4life.ac.za", "givenName": "Sipho", "surname": "Zulu"}
    )
    with microsoft_app.test_request_context("/auth/azure/authorized"):
        response = _on_microsoft_authorized(blueprint, {"access_token": "t"})
        assert response.status_code == 302 and response.location.endswith("/register")
        pending = dict(session["ms_pending"])
    assert pending == {"id": "ms-123", "email": "sipho@dut4life.ac.za", "first_name": "Sipho", "last_name": "Zulu"}

    with client.session_transaction() as sess:
        sess["ms_pending"] = pending
    page = client.get("/register").get_data(as_text=True)
    assert "sipho@dut4life.ac.za" in page and 'name="Password"' not in page
    assert 'value="Sipho"' in page  # name pre-filled

    registration_data.pop("Password"), registration_data.pop("ConfirmPassword")
    registration_data["Email"] = "attacker@evil.example"  # cannot be changed
    assert client.post("/register", data=registration_data).status_code == 302
    user = _only_user()
    assert (user.Email, user.MicrosoftId, user.PasswordHash) == ("sipho@dut4life.ac.za", "ms-123", None)
    assert client.get("/dashboard").status_code == 200


def test_returning_microsoft_user_is_signed_straight_in(microsoft_app):
    _add_user("back@example.com", microsoft_id="ms-777")
    with microsoft_app.test_request_context("/"):
        response = complete_microsoft_login({"id": "ms-777", "mail": "back@example.com"})
    assert response.status_code == 302 and response.location.endswith("/dashboard")


def test_existing_email_is_never_auto_linked_to_a_microsoft_identity(microsoft_app):
    from flask import session

    _add_user("taken@example.com", password="Str0ng!Pass")
    with microsoft_app.test_request_context("/"):
        response = complete_microsoft_login({"id": "someone-else", "mail": "taken@example.com"})
        assert response.location.endswith("/login") and "ms_pending" not in session
    assert db.session.scalars(select(User)).one().MicrosoftId is None


def test_microsoft_failures_land_on_the_login_page(microsoft_app):
    with microsoft_app.test_request_context("/"):
        assert _on_microsoft_authorized(MagicMock(), None).location.endswith("/login")
        broken = MagicMock()
        broken.session.get.side_effect = RuntimeError("network down")
        assert _on_microsoft_authorized(broken, {"access_token": "t"}).location.endswith("/login")
        assert complete_microsoft_login({"id": "x"}).location.endswith("/login")  # no email shared


# ------------------------------------------------------------------------------------------ factory_boy scenarios
from tests.factories import UserFactory  # noqa: E402


def test_sign_in_with_a_factory_user_and_sign_out(app):
    user = UserFactory(password="Str0ng!Pass", Email="Sipho@Example.com")
    client = app.test_client()
    response = client.post(
        "/login", data={"Email": "sipho@example.COM", "Password": "Str0ng!Pass"}
    )  # e-mail is case-insensitive
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200
    client.post("/logout")
    assert client.get("/dashboard").status_code == 302
    db.session.refresh(user)


def test_wrong_passwords_and_unknown_accounts_look_identical(app):
    UserFactory(password="Str0ng!Pass", Email="real@example.com")
    client = app.test_client()
    wrong = client.post("/login", data={"Email": "real@example.com", "Password": "Wrong1!pass"})
    unknown = client.post("/login", data={"Email": "nobody@example.com", "Password": "Wrong1!pass"})
    assert wrong.status_code == unknown.status_code == 401
    assert b"Incorrect email or password" in wrong.data and b"Incorrect email or password" in unknown.data


def test_an_account_without_a_password_cannot_sign_in_that_way(app):
    UserFactory(Email="microsoft@example.com")  # no password: a Microsoft-only account
    response = app.test_client().post("/login", data={"Email": "microsoft@example.com", "Password": "Str0ng!Pass"})
    assert response.status_code == 401


def test_a_deactivated_account_is_told_so_only_after_the_right_password(app):
    UserFactory(password="Str0ng!Pass", Email="off@example.com", IsActive=False)
    client = app.test_client()
    assert client.post("/login", data={"Email": "off@example.com", "Password": "Str0ng!Pass"}).status_code == 403
    assert client.post("/login", data={"Email": "off@example.com", "Password": "Wrong1!pass"}).status_code == 401


def test_the_password_hash_is_bcrypt_and_never_the_password(app):
    user = UserFactory(password="Str0ng!Pass")
    assert user.PasswordHash.startswith("$2b$") and "Str0ng" not in user.PasswordHash


# ------------------------------------------------------------------- changing a password ends the other sessions
def _sign_in(app, email="real@example.com", password="Str0ng!Pass", remember=False):
    client = app.test_client()
    data = {"Email": email, "Password": password}
    if remember:
        data["Remember"] = "y"
    assert client.post("/login", data=data).status_code == 302
    return client


def test_changing_your_password_signs_out_your_other_devices_but_not_this_one(app):
    UserFactory(password="Str0ng!Pass", Email="real@example.com")
    phone, laptop = _sign_in(app), _sign_in(app)
    assert phone.get("/profile").status_code == 200 and laptop.get("/profile").status_code == 200

    changed = laptop.post(
        "/profile/password",
        data={
            "pw-CurrentPassword": "Str0ng!Pass",
            "pw-NewPassword": "N3w!Password",
            "pw-ConfirmPassword": "N3w!Password",
        },
    )
    assert changed.status_code == 302
    assert laptop.get("/profile").status_code == 200  # this browser carries on
    assert phone.get("/profile").status_code == 302  # the other device must sign in again
    assert _sign_in(app, password="N3w!Password").get("/profile").status_code == 200


def test_a_remember_me_cookie_stops_working_when_the_password_changes(app):
    user = UserFactory(password="Str0ng!Pass", Email="real@example.com")
    stolen = _sign_in(app, remember=True)
    cookie = stolen.get_cookie("remember_token")
    assert cookie is not None

    user.set_password("N3w!Password")
    db.session.commit()
    thief = app.test_client()
    thief.set_cookie("remember_token", cookie.value)  # only the remember-me cookie, no session
    assert thief.get("/profile").status_code == 302


def test_the_signed_in_browser_that_changes_its_password_keeps_remember_me(app):
    UserFactory(password="Str0ng!Pass", Email="real@example.com")
    client = _sign_in(app, remember=True)
    response = client.post(
        "/profile/password",
        data={
            "pw-CurrentPassword": "Str0ng!Pass",
            "pw-NewPassword": "N3w!Password",
            "pw-ConfirmPassword": "N3w!Password",
        },
    )
    assert any("remember_token" in header for header in response.headers.getlist("Set-Cookie"))


def test_an_unknown_address_and_a_microsoft_only_account_both_spend_a_bcrypt_check(app, monkeypatch):
    from app.routes import auth as auth_routes

    UserFactory(Email="microsoft@example.com")  # no password
    spent = []
    monkeypatch.setattr(auth_routes, "_spend_time_like_a_real_check", lambda password: spent.append(password))
    client = app.test_client()
    client.post("/login", data={"Email": "nobody@example.com", "Password": "Str0ng!Pass"})
    client.post("/login", data={"Email": "microsoft@example.com", "Password": "Str0ng!Pass"})
    assert len(spent) == 2


def test_any_13_digits_are_accepted_as_an_id_number(client, registration_data):
    registration_data.update(SAIdNumber="1234567890123", Gender="Male")  # not a real SA ID: no date, bad check digit
    assert _register(client, registration_data).status_code == 302
    user = _only_user()
    assert user.SAIdLast4 == "0123" and user.DateOfBirth is None and user.Gender == "Male"
