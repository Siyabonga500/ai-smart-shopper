"""Step 19: the strict validators in app/utils/validators.py and how the forms show their errors."""

from datetime import date
from types import SimpleNamespace

import pytest

from app.utils.validators import (
    extract_dob_and_gender,
    validate_email,
    validate_password,
    validate_sa_id,
    validate_sa_phone,
)
from tests.conftest import make_sa_id

TODAY = date(2026, 9, 20)


# ------------------------------------------------------------------------------------ SA ID
def test_validate_sa_id_returns_the_cleaned_number():
    assert validate_sa_id("800101 5009-087", today=TODAY) == "8001015009087"


@pytest.mark.parametrize(
    "bad, message",
    [
        (None, "13 digits"),
        ("", "13 digits"),
        ("800101500908", "13 digits"),  # 12 digits
        ("80010150090871", "13 digits"),  # 14 digits
        ("80010150090AB", "13 digits"),
        ("٨٠٠١٠١٥٠٠٩٠٨٧", "13 digits"),  # Arabic-Indic digits are not digits here
        ("8001015009088", "not valid"),  # Luhn
        (make_sa_id("900231"), "date of birth"),  # 31 February
        (make_sa_id("901301"), "date of birth"),  # month 13
        (make_sa_id("900200"), "date of birth"),  # day 0
        (make_sa_id("900215", citizen="2"), "citizenship"),  # citizenship digit must be 0 or 1
        (make_sa_id("900215", citizen="9"), "citizenship"),
    ],
)
def test_validate_sa_id_rejects(bad, message):
    with pytest.raises(ValueError, match=message):
        validate_sa_id(bad, today=TODAY)


@pytest.mark.parametrize("citizen", ["0", "1"])
def test_both_citizenship_digits_are_accepted(citizen):
    assert validate_sa_id(make_sa_id("900215", citizen=citizen), today=TODAY)


def test_a_future_birth_date_is_read_as_the_previous_century_not_refused():
    """A two-digit year cannot be in the future, so 270101 means 1927 (matches the JS side)."""
    dob, _ = extract_dob_and_gender(make_sa_id("270101", "5000"), today=TODAY)
    assert dob == date(1927, 1, 1)


@pytest.mark.parametrize("seq, gender", [("0000", "Female"), ("4999", "Female"), ("5000", "Male"), ("9999", "Male")])
def test_extract_dob_and_gender(seq, gender):
    dob, found = extract_dob_and_gender(make_sa_id("950505", seq), today=TODAY)
    assert (dob, found) == (date(1995, 5, 5), gender)


def test_extract_dob_and_gender_known_number():
    assert extract_dob_and_gender("8001015009087", today=TODAY) == (date(1980, 1, 1), "Male")


def test_extract_dob_and_gender_refuses_an_invalid_id():
    with pytest.raises(ValueError):
        extract_dob_and_gender("8001015009088", today=TODAY)


# ------------------------------------------------------------------------------------ phone
@pytest.mark.parametrize(
    "number", ["0821234567", "0721234567", "0612345678", "082 123 4567", "082-123-4567", "(082)1234567"]
)
def test_validate_sa_phone_accepts_and_cleans(number):
    assert validate_sa_phone(number) == number.translate(str.maketrans("", "", " -()"))


@pytest.mark.parametrize(
    "number",
    [
        None,
        "",
        "082123456",
        "08212345678",  # too short / too long
        "0311234567",
        "0511234567",
        "0111234567",  # a landline, not 06/07/08
        "0921234567",
        "0021234567",  # prefix is not 06/07/08
        "+27821234567",
        "27821234567",  # local format only
        "1821234567",
        "abcdefghij",
        "082123456a",
        "٠٨٢١٢٣٤٥٦٧",  # Arabic-Indic digits
    ],
)
def test_validate_sa_phone_rejects(number):
    with pytest.raises(ValueError, match="10 digits"):
        validate_sa_phone(number)


# ---------------------------------------------------------------------------------- password
@pytest.mark.parametrize("password", ["Str0ng!Pass", "Abcdef1!", "Zulu_Str0ng", "Pa55word€", "Ünï-c0de-Pass"])
def test_validate_password_accepts(password):
    assert validate_password(password) == password


@pytest.mark.parametrize(
    "password, fragment",
    [
        (None, "at least 8 characters"),
        ("Sh0rt!", "at least 8 characters"),
        ("alllower1!", "one uppercase letter"),
        ("ALLUPPER1!", "one lowercase letter"),
        ("NoNumber!!", "one number"),
        ("NoSymbol123", "one symbol"),
        ("Pass١٢٣!word", "one number"),  # non-ASCII digits are not "a number" here
        ("Aa1!" + "x" * 80, "72 bytes"),
    ],
)
def test_validate_password_rejects(password, fragment):
    with pytest.raises(ValueError, match=fragment) as excinfo:
        validate_password(password)
    assert str(excinfo.value).startswith("Your password needs ")


def test_the_password_message_lists_everything_missing_at_once():
    with pytest.raises(ValueError) as excinfo:
        validate_password("abc")
    text = str(excinfo.value)
    assert all(part in text for part in ("8 characters", "uppercase", "number", "symbol"))
    assert "lowercase" not in text


# ------------------------------------------------------------------------------------ email
@pytest.mark.parametrize(
    "email, expected",
    [
        ("thandi@example.com", "thandi@example.com"),
        ("  Thandi.Nkosi@Example.CO.ZA ", "thandi.nkosi@example.co.za"),
        ("t+shop@dut4life.ac.za", "t+shop@dut4life.ac.za"),
        ("o'neil@example.org", "o'neil@example.org"),
        ("a@b.co", "a@b.co"),
        ("x_y-z@sub.domain.example.com", "x_y-z@sub.domain.example.com"),
        ("student@bücher.de", "student@xn--bcher-kva.de"),  # internationalised domains are stored as punycode
    ],
)
def test_validate_email_accepts_and_normalises(email, expected):
    assert validate_email(email) == expected


@pytest.mark.parametrize(
    "email",
    [
        None,
        "",
        "   ",
        "plain",
        "@example.com",
        "name@",
        "name@example",
        "name@@example.com",
        "a@b@example.com",
        "name@.com",
        "name@example..com",
        "name@-example.com",
        "name@example-.com",
        "name@example.c",
        "name@example.123",
        "name@exa mple.com",
        "na me@example.com",
        ".name@example.com",
        "name.@example.com",
        "na..me@example.com",
        "name@example.com.",
        "name@" + "a" * 64 + ".com",  # a trailing dot is tidied; a 64-letter label is too long
        "a" * 65 + "@example.com",
        "a" * 250 + "@example.com",
        "<name@example.com>",
        'na"me@example.com',
        "name@ex_ample.com",
    ],
)
def test_validate_email_rejects(email):
    if email == "name@example.com.":
        assert validate_email(email) == "name@example.com"
        return
    with pytest.raises(ValueError, match="valid email"):
        validate_email(email)


class _FakeDns:
    """Stand-in for dns.resolver so no test ever touches the network."""

    def __init__(self, behaviour):
        import dns.exception
        import dns.resolver

        self.dns, self.behaviour = SimpleNamespace(exception=dns.exception, resolver=dns.resolver), behaviour

    def install(self, monkeypatch):
        resolver_module, behaviour, calls = self.dns.resolver, self.behaviour, []

        class Resolver:
            timeout = lifetime = 0

            def resolve(self, domain, record_type):
                calls.append((domain, record_type))
                outcome = behaviour.get(record_type, resolver_module.NoAnswer())
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        monkeypatch.setattr(resolver_module, "Resolver", Resolver)
        return calls


def test_dns_check_accepts_a_domain_with_mx(monkeypatch):
    calls = _FakeDns({"MX": ["10 mail.example.com."]}).install(monkeypatch)
    assert validate_email("a@example.com", check_dns=True) == "a@example.com"
    assert calls == [("example.com", "MX")]


def test_dns_check_falls_back_to_an_address_record(monkeypatch):
    calls = _FakeDns({"A": ["93.184.216.34"]}).install(monkeypatch)
    assert validate_email("a@example.com", check_dns=True)
    assert [record for _, record in calls] == ["MX", "A"]


def test_dns_check_rejects_a_domain_that_does_not_exist(monkeypatch):
    import dns.resolver

    _FakeDns({"MX": dns.resolver.NXDOMAIN()}).install(monkeypatch)
    with pytest.raises(ValueError, match="could not find that email domain"):
        validate_email("a@no-such-domain.example", check_dns=True)


def test_dns_check_rejects_a_domain_with_no_mail_or_address_records(monkeypatch):
    _FakeDns({}).install(monkeypatch)
    with pytest.raises(ValueError, match="could not find that email domain"):
        validate_email("a@example.com", check_dns=True)


@pytest.mark.parametrize("problem", ["Timeout", "NoNameservers", "OSError"])
def test_a_dns_outage_never_blocks_a_sign_up(monkeypatch, problem):
    import dns.exception
    import dns.resolver

    error = {
        "Timeout": dns.exception.Timeout(),
        "NoNameservers": dns.resolver.NoNameservers(),
        "OSError": OSError("network unreachable"),
    }[problem]
    _FakeDns({"MX": error}).install(monkeypatch)
    assert validate_email("a@example.com", check_dns=True) == "a@example.com"


def test_dns_is_not_asked_unless_requested(monkeypatch):
    calls = _FakeDns({}).install(monkeypatch)
    assert validate_email("a@example.com") == "a@example.com"
    assert calls == []


def test_a_bad_address_is_refused_before_any_dns_lookup(monkeypatch):
    calls = _FakeDns({"MX": ["x"]}).install(monkeypatch)
    with pytest.raises(ValueError):
        validate_email("nonsense", check_dns=True)
    assert calls == []


# -------------------------------------------------------------------------------- the forms
def _register(client, data):
    return client.post("/register", data=data)


def test_registration_form_shows_the_email_rule_inline(client, registration_data):
    registration_data["Email"] = "thandi@example"
    body = _register(client, registration_data).get_data(as_text=True)
    assert "Enter a valid email address, for example name@example.com." in body


def test_registration_form_checks_dns_only_when_switched_on(app, client, registration_data, monkeypatch):
    import dns.resolver

    _FakeDns({"MX": dns.resolver.NXDOMAIN()}).install(monkeypatch)
    app.config["EMAIL_DNS_CHECK"] = True
    body = _register(client, registration_data).get_data(as_text=True)
    assert "could not find that email domain" in body


def test_registration_form_shows_the_citizenship_rule_inline(client, registration_data):
    registration_data["SAIdNumber"] = make_sa_id("900215", "5123", citizen="2")
    body = _register(client, registration_data).get_data(as_text=True)
    assert "citizenship digit" in body


def test_registration_form_names_the_missing_password_rules(client, registration_data):
    registration_data.update(Password="ALLUPPER1!", ConfirmPassword="ALLUPPER1!")
    body = _register(client, registration_data).get_data(as_text=True)
    assert "Your password needs one lowercase letter." in body
