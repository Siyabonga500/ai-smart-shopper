"""app/static/js/validators.js and app/utils/validators.py must give the same verdict on every phone number,
password and email address, so the form never says "fine" and then gets refused by the server (or the reverse)."""

import json
import random
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest

from app.utils.passwords import password_problems
from app.utils.validators import validate_email, validate_sa_phone

JS_FILE = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "validators.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

NODE_RUNNER = r"""
const V = require(process.argv[1]);
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = {
  phones: input.phones.map((v) => V.validatePhone(v)),
  passwords: input.passwords.map((v) => V.passwordProblems(v)),
  emails: input.emails.map((v) => V.validateEmail(v)),
};
process.stdout.write(JSON.stringify(out));
"""

PHONES = [
    "0821234567",
    "0721234567",
    "0612345678",
    "082 123 4567",
    "082-123-4567",
    "(082)1234567",
    " 0821234567 ",
    "082123456",
    "08212345678",
    "0311234567",
    "0511234567",
    "0921234567",
    "+27821234567",
    "27821234567",
    "abcdefghij",
    "082123456a",
    "٠٨٢١٢٣٤٥٦٧",
    "",
    "   ",
    "0",
    "08",
    "0821234567\n",
    "082\t123\t4567",
]

EMAILS = [
    "thandi@example.com",
    "  Thandi.Nkosi@Example.CO.ZA ",
    "t+shop@dut4life.ac.za",
    "o'neil@example.org",
    "a@b.co",
    "x_y-z@sub.domain.example.com",
    "student@bücher.de",
    "student@münchen.example",
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
    "name@" + "a" * 63 + ".com",
    "name@" + "a" * 64 + ".com",
    "a" * 64 + "@example.com",
    "a" * 65 + "@example.com",
    "a" * 250 + "@example.com",
    "<name@example.com>",
    'na"me@example.com',
    "name@ex_ample.com",
    "name@example.xn--p1ai",
    "name@example.xn--a",
    "",
    "   ",
    "a@b.c.d.e.f.g.example.com",
    "UPPER@EXAMPLE.COM",
    "name@[127.0.0.1]",
    "name@127.0.0.1",
    "nam€@example.com",
    "name@exam­ple.com",
    "name@例え.jp",
]


def _password_cases() -> list[str]:
    cases = [
        "Str0ng!Pass",
        "Abcdef1!",
        "Zulu_Str0ng",
        "Pa55word€",
        "Ünï-c0de-Pass",
        "Sh0rt!",
        "alllower1!",
        "ALLUPPER1!",
        "NoNumber!!",
        "NoSymbol123",
        "Pass١٢٣!word",
        "Aa1!" + "x" * 80,
        "",
        "        ",
        "Aa1!Aa1!",
        "Aa1! Aa1!",
        "ǅǅǅǅ1!ab",
        "ªªªªªª1!A",
        "ⅧⅧⅧⅧ1!aA",
        "😀Aa1😀xyz",
        "Aa1_bcdef",
        "Aa1~bcdef",
        "Aa1`bcdef",
        "Aa1+bcdef",
        "Aa1<bcdef",
        "٣٤٥٦٧٨٩٠Aa!",
        "Ａａ１！ｂｃｄｅ",
        "a" * 24 + "A1!" + "é" * 20,
    ]
    # every single character, dropped into a password that otherwise satisfies nothing but the length rule
    for code in (
        list(range(0x20, 0x3000))
        + list(range(0xA000, 0xA800))
        + list(range(0xFF00, 0xFFF0))
        + list(range(0x1F300, 0x1F400))
    ):
        if 0xD800 <= code <= 0xDFFF or unicodedata.category(chr(code)) == "Cn":
            continue  # surrogates; and characters Python's older Unicode tables do not know yet (Node's may)
        cases.append("xxxxxxx" + chr(code))
    rng = random.Random(20260920)
    alphabet = "abcXYZ019!@#_ -é€Ω٣ǅ"
    cases += ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14))) for _ in range(500)]
    return cases


@pytest.fixture(scope="module")
def cases_and_js():
    cases = {"phones": PHONES, "passwords": _password_cases(), "emails": EMAILS}
    completed = subprocess.run(
        ["node", "-e", NODE_RUNNER, str(JS_FILE)],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return cases, json.loads(completed.stdout)


def _verdict(check, value):
    try:
        return {"valid": True, "value": check(value)}
    except ValueError as exc:
        return {"valid": False, "error": str(exc)}


def test_phone_rules_agree(cases_and_js):
    cases, js = cases_and_js
    for value, actual in zip(cases["phones"], js["phones"]):
        assert actual == _verdict(validate_sa_phone, value), repr(value)


def test_password_rules_agree_for_every_case_and_every_character(cases_and_js):
    cases, js = cases_and_js
    assert len(cases["passwords"]) > 12000
    mismatches = [
        (value, password_problems(value), actual)
        for value, actual in zip(cases["passwords"], js["passwords"])
        if password_problems(value) != actual
    ]
    assert not mismatches, f"{len(mismatches)} disagreements, first: {mismatches[0]!r}"


def test_email_rules_agree(cases_and_js):
    cases, js = cases_and_js
    mismatches = [
        (value, _verdict(validate_email, value), actual)
        for value, actual in zip(cases["emails"], js["emails"])
        if actual != _verdict(validate_email, value)
    ]
    assert not mismatches, f"{len(mismatches)} disagreements, first: {mismatches[0]!r}"


def test_the_case_sets_exercise_both_outcomes(cases_and_js):
    _, js = cases_and_js
    for key in ("phones", "emails"):
        assert sum(item["valid"] for item in js[key]) >= 5
        assert sum(not item["valid"] for item in js[key]) >= 5
    assert sum(not item for item in js["passwords"]) >= 5  # no problems = acceptable
    assert sum(bool(item) for item in js["passwords"]) >= 100
