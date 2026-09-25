import pytest
from flask import g

from app import create_app
from app.extensions import cache, db
from app.models import User


def config_for_testing():
    """The class ``create_app("testing")`` really uses. tests/test_config.py reloads ``app.config``, after which
    ``from app.config import TestConfig`` would hand out a different (unused) class, so ask the factory's own table."""
    import app as package

    return package.config_by_name["testing"]


def forget_login_between_requests(app):
    """The fixtures keep one app context open, so ``g`` outlives a request; without this the first signed-in user
    would stay "logged in" for every later request and client in the same test."""

    def _forget_the_previous_request_user():
        g.pop("_login_user", None)

    # First in line: Flask-Limiter's own hook (added by create_app) already asks who is signed in.
    app.before_request_funcs.setdefault(None, []).insert(0, _forget_the_previous_request_user)
    return app


@pytest.fixture
def app():
    app = forget_login_between_requests(create_app("testing"))

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def real_cache(app):
    """The test config uses NullCache; caching tests need a cache that keeps things."""
    cache.init_app(app, config={"CACHE_TYPE": "SimpleCache"})
    cache.clear()
    return cache


@pytest.fixture
def make_user(app):
    counter = {"n": 0}

    def _make(first="Thabo", last="Mokoena", email=None, **extra):
        counter["n"] += 1
        user = User(
            FirstName=first,
            LastName=last,
            Email=email or f"user{counter['n']}@example.com",
            MicrosoftId=f"ms-{counter['n']}",
            **extra,
        )
        db.session.add(user)
        db.session.commit()
        return user

    return _make


@pytest.fixture
def login(client):
    """Log ``user`` in by writing the Flask-Login session keys directly."""

    def _login(user):
        with client.session_transaction() as session:
            session["_user_id"] = user.get_id()
            session["_fresh"] = True
        return client

    return _login


def make_sa_id(yymmdd="900215", seq="5123", citizen="0", legacy="8") -> str:
    """A structurally valid 13-digit SA ID (correct Luhn check digit) for tests."""
    body = f"{yymmdd}{seq}{citizen}{legacy}"
    for check in "0123456789":
        digits = body + check
        total = 0
        for index, char in enumerate(reversed(digits)):
            value = int(char)
            if index % 2 == 1:
                value = value * 2 - 9 if value * 2 > 9 else value * 2
            total += value
        if total % 10 == 0:
            return digits
    raise AssertionError("unreachable")


@pytest.fixture
def sa_id():
    return make_sa_id


@pytest.fixture
def registration_data(sa_id):
    """A complete, valid registration form as the browser would post it."""
    return {
        "FirstName": "Thandi",
        "LastName": "Nkosi",
        "Email": "22226534@dut4life.ac.za",
        "CellphoneNumber": "0821234567",
        "SAIdNumber": sa_id("020315", "0123"),  # 15 March 2002, female
        "Gender": "",
        "Race": "African",
        "Residence": "campbell-hall",
        "ResidentialAddress": "",
        "Latitude": "",
        "Longitude": "",
        "Password": "Str0ng!Pass",
        "ConfirmPassword": "Str0ng!Pass",
        "AcceptTerms": "y",
    }


@pytest.fixture
def make_password_user(app):
    """A user who can sign in with email + password."""

    def _make(email="student@example.com", password="Str0ng!Pass", **extra):
        user = User(FirstName=extra.pop("first", "Sipho"), LastName=extra.pop("last", "Dlamini"), Email=email, **extra)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user

    return _make
