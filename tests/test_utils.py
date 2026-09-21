import re
from decimal import Decimal

import pytest

from app.utils import (
    allowed_image,
    format_zar,
    generate_id,
    haversine_km,
    is_valid_sa_cellphone,
    map_center_for,
    normalise_sa_cellphone,
    parse_zar_amount,
    to_decimal,
)

NBSP = " "


@pytest.mark.parametrize(
    "value, kwargs, expected",
    [
        (1750, {}, f"R1{NBSP}750"),
        (Decimal("29.99"), {}, "R29.99"),
        (280.9, {}, "R280.90"),
        (-72.88, {}, "-R72.88"),
        (-50, {}, "-R50"),
        (0, {}, "R0"),
        (None, {}, "R0"),
        (1750, {"cents": True}, f"R1{NBSP}750.00"),
        (2.5, {"cents": False}, "R3"),
        (1234567.5, {}, f"R1{NBSP}234{NBSP}567.50"),
    ],
)
def test_format_zar(value, kwargs, expected):
    assert format_zar(value, **kwargs) == expected


def test_haversine_zero_and_durban_to_johannesburg():
    assert haversine_km(-29.8587, 31.0218, -29.8587, 31.0218) == pytest.approx(0)
    distance = haversine_km(-29.8587, 31.0218, -26.2041, 28.0473)
    assert 480 < distance < 520


def test_map_center_falls_back_to_durban(app):
    with app.app_context():
        durban = {"lat": -29.8587, "lng": 31.0218}
        assert map_center_for() == durban
        assert map_center_for(None, None) == durban
        assert map_center_for(999, 31.0) == durban  # invalid latitude
        assert map_center_for("abc", "def") == durban
        assert map_center_for(-26.2, 28.0) == {"lat": -26.2, "lng": 28.0}


@pytest.mark.parametrize("prefix", ["USR", "BGT", "SBG", "LST", "ITM", "PRF"])
def test_generate_id_is_a_prefixed_uuid_that_fits_string_30(prefix):
    ids = {generate_id(prefix) for _ in range(500)}
    assert len(ids) == 500  # unique
    for value in ids:
        assert re.fullmatch(rf"{prefix}-[0-9a-f]{{26}}", value)
        assert len(value) == 30  # uses every character String(30) allows


def test_generate_id_uppercases_the_prefix():
    assert generate_id("usr").startswith("USR-")


def test_generate_id_rejects_a_prefix_that_leaves_too_little_uuid():
    with pytest.raises(ValueError):
        generate_id("A" * 20)


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, Decimal("0")),
        (5, Decimal("5")),
        (0.1, Decimal("0.1")),
        ("12.50", Decimal("12.50")),
        (Decimal("3.20"), Decimal("3.20")),
        ("junk", Decimal("0")),
    ],
)
def test_to_decimal(value, expected):
    assert to_decimal(value) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("071 234 5678", "+27712345678"),
        ("0712345678", "+27712345678"),
        ("+27 71 234 5678", "+27712345678"),
        ("27712345678", "+27712345678"),
        ("(082) 555-1234", "+27825551234"),
        ("031 123 4567", None),  # landline
        ("12345", None),
        ("", None),
        (None, None),
    ],
)
def test_sa_cellphone(raw, expected):
    assert normalise_sa_cellphone(raw) == expected
    assert is_valid_sa_cellphone(raw) is (expected is not None)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("R1 750", Decimal("1750.00")),
        (f"R1{NBSP}750.50", Decimal("1750.50")),
        ("1,200", Decimal("1200.00")),
        (300, Decimal("300.00")),
        ("0", Decimal("0.00")),
    ],
)
def test_parse_zar_amount(raw, expected):
    assert parse_zar_amount(raw) == expected


@pytest.mark.parametrize("bad", [None, "", "abc", "-5", "R-5", "NaN", "Infinity"])
def test_parse_zar_amount_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        parse_zar_amount(bad)


def test_allowed_image():
    allowed = {"jpg", "jpeg", "png", "webp"}
    assert allowed_image("me.PNG", allowed)
    assert allowed_image("photo.webp", allowed)
    assert not allowed_image("script.exe", allowed)
    assert not allowed_image("noextension", allowed)
    assert not allowed_image(None, allowed)


def test_phone_numbers_and_amounts_take_ascii_digits_only():
    arabic = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
    assert normalise_sa_cellphone("0" + "821234567".translate(arabic)) is None
    assert normalise_sa_cellphone("+27" + "821234567".translate(arabic)) is None
    assert normalise_sa_cellphone("0821234567") == "+27821234567"
    from app.utils.money import parse_amount

    assert parse_amount("١٢٣") is None and parse_amount("R١,٥٠٠") is None
