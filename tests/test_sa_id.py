from datetime import date

import pytest

from app.utils.passwords import password_problems
from app.utils.sa_id import (
    SAIdError,
    clean_sa_id,
    hash_sa_id,
    last_four,
    luhn_valid,
    mask_sa_id,
    parse_sa_id,
)
from app.utils.validators import is_valid_sa_mobile_10
from tests.conftest import make_sa_id

TODAY = date(2026, 9, 20)


def test_known_valid_id_parses():
    info = parse_sa_id("8001015009087", today=TODAY)
    assert info.date_of_birth == date(1980, 1, 1)
    assert info.gender == "Male"
    assert info.is_citizen is True


@pytest.mark.parametrize("seq, gender", [("0000", "Female"), ("4999", "Female"), ("5000", "Male"), ("9999", "Male")])
def test_gender_comes_from_digits_7_to_10_not_the_date(seq, gender):
    assert parse_sa_id(make_sa_id("950505", seq), today=TODAY).gender == gender


@pytest.mark.parametrize(
    "yymmdd, expected",
    [
        ("050101", date(2005, 1, 1)),  # 05 -> 2005 (not in the future)
        ("990101", date(1999, 1, 1)),
        ("260920", date(2026, 9, 20)),  # today counts
        ("260921", date(1926, 9, 21)),  # tomorrow in 2026, so it must be 1926
        ("000229", date(2000, 2, 29)),  # 2000 was a leap year
    ],
)
def test_century_is_the_latest_date_not_in_the_future(yymmdd, expected):
    assert parse_sa_id(make_sa_id(yymmdd), today=TODAY).date_of_birth == expected


def test_permanent_resident_flag():
    assert parse_sa_id(make_sa_id("900101", "5000", citizen="1"), today=TODAY).is_citizen is False


@pytest.mark.parametrize(
    "bad, message",
    [
        ("", "13 digits"),
        ("123", "13 digits"),
        ("80010150090871", "13 digits"),
        ("80010150090AB", "13 digits"),
        ("8001015009088", "not valid"),  # wrong check digit
        (make_sa_id("900231"), "date of birth"),  # 31 February
        (make_sa_id("901301"), "date of birth"),  # month 13
    ],
)
def test_invalid_ids_raise_friendly_errors(bad, message):
    with pytest.raises(SAIdError, match=message):
        parse_sa_id(bad, today=TODAY)


def test_spaces_and_dashes_are_ignored():
    assert clean_sa_id("800101 5009-087") == "8001015009087"
    assert parse_sa_id("800101 5009 087", today=TODAY).gender == "Male"


def test_luhn():
    assert luhn_valid("4539578763621486")
    assert not luhn_valid("4539578763621487")
    assert not luhn_valid("12a4")


def test_hash_and_mask_never_reveal_the_number():
    digest = hash_sa_id("8001015009087", "pepper")
    assert len(digest) == 64 and "8001015009087" not in digest
    assert digest == hash_sa_id("800101 5009 087", "pepper")  # formatting does not matter
    assert digest != hash_sa_id("8001015009087", "other-pepper")
    assert last_four("8001015009087") == "9087"
    assert mask_sa_id("9087") == "*********9087"
    assert mask_sa_id(None) == "—"


@pytest.mark.parametrize(
    "number, ok",
    [
        ("0821234567", True),
        ("0721234567", True),
        ("0612345678", True),
        ("082 123 4567", True),
        ("082123456", False),
        ("08212345678", False),
        ("0311234567", False),
        ("+27821234567", False),
        ("abcdefghij", False),
    ],
)
def test_ten_digit_mobile(number, ok):
    assert is_valid_sa_mobile_10(number) is ok


@pytest.mark.parametrize(
    "password, missing",
    [
        ("Str0ng!Pass", []),
        ("short1!A", []),
        ("Sh0rt!", ["at least 8 characters"]),
        ("alllower1!", ["one uppercase letter"]),
        ("ALLUPPER1!", ["one lowercase letter"]),
        ("NoNumber!!", ["one number"]),
        ("NoSymbol123", ["one symbol (for example ! @ # $ %)"]),
        (
            "",
            [
                "at least 8 characters",
                "one uppercase letter",
                "one lowercase letter",
                "one number",
                "one symbol (for example ! @ # $ %)",
            ],
        ),
    ],
)
def test_password_policy(password, missing):
    assert password_problems(password) == missing


def test_password_over_72_bytes_is_refused():
    assert "no more than 72 bytes" in password_problems("Aa1!" + "x" * 80)


ARABIC_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def test_only_ascii_digits_count_so_one_id_cannot_be_registered_twice():
    """Python's \\d and str.isdigit() also accept other scripts' digits, which would hash differently from the ASCII ID."""
    valid = "8001015009087"
    assert parse_sa_id(valid, today=TODAY)
    with pytest.raises(SAIdError):
        parse_sa_id(valid.translate(ARABIC_INDIC), today=TODAY)
    assert not luhn_valid(valid.translate(ARABIC_INDIC))
    assert not luhn_valid("²" * 13)
