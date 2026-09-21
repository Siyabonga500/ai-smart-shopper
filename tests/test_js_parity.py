"""The browser (app/static/js/sa-id.js) and the server (app/utils/sa_id.py) must agree on every ID.

If they drift, a student sees "valid" in the form and then a server error, or the reverse.
Both implementations are run over the same cases, using node for the JavaScript side.
"""

import json
import random
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from app.utils.sa_id import SAIdError, clean_sa_id, luhn_valid, parse_sa_id

JS_FILE = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "sa-id.js"
TODAY = date(2026, 9, 20)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def make_sa_id(yymmdd: str, seq: str, citizen: str = "0", legacy: str = "8") -> str:
    """Append the Luhn check digit that makes the ID pass (the date part may still be nonsense)."""
    body = f"{yymmdd}{seq}{citizen}{legacy}"
    return next(body + check for check in "0123456789" if luhn_valid(body + check))


NODE_RUNNER = r"""
const SAId = require(process.argv[1]);
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const today = new Date(input.today[0], input.today[1] - 1, input.today[2]);
const out = input.values.map(function (value) {
  const parsed = SAId.parse(value, today);
  return {
    clean: SAId.clean(value),
    luhn: SAId.luhnValid(SAId.clean(value)),
    valid: parsed.valid,
    error: parsed.error || null,
    dateOfBirth: parsed.dateOfBirth || null,
    gender: parsed.gender || null,
    isCitizen: parsed.valid ? parsed.isCitizen : null,
  };
});
process.stdout.write(JSON.stringify(out));
"""


def _cases() -> list[str]:
    cases = [
        make_sa_id("900215", "5123"),  # male, 1990
        make_sa_id("050101", "0001"),  # female, 2005 (century inference)
        make_sa_id("991231", "4999", "1"),  # last female number, permanent resident
        make_sa_id("900215", "5123", "2"),  # citizenship digit must be 0 or 1
        make_sa_id("900215", "5123", "9"),
        make_sa_id("000229", "5000"),  # 29 Feb 2000 exists (leap year)
        make_sa_id("000229", "4999"),
        make_sa_id("260920", "5000"),  # born today -> 2026
        make_sa_id("260921", "5000"),  # tomorrow -> falls back to 1926
        make_sa_id("270101", "5000"),  # would be in the future -> 1927
        make_sa_id("010229", "5000"),  # 29 Feb 2001 does not exist in either century
        make_sa_id("000431", "5000"),  # 31 April
        make_sa_id("901301", "5000"),  # month 13
        make_sa_id("900200", "5000"),  # day 0
        "9002155123088",  # fixed number, probably a bad check digit
        "",
        "   ",
        "123",
        "abcdefghijklm",
        "90021551230881",
        "900215512308",
        "9002 1551 2308 8",  # spaces are ignored
        "900215-5123-08-8",  # dashes are ignored
        " " + make_sa_id("900215", "5123") + " ",
    ]
    rng = random.Random(20260920)
    for _ in range(300):
        cases.append(
            make_sa_id(
                f"{rng.randint(0, 99):02d}{rng.randint(0, 13):02d}{rng.randint(0, 32):02d}",
                f"{rng.randint(0, 9999):04d}",
                rng.choice("0123"),  # 2 and 3 are refused, so both sides must agree on those too
            )
        )
    for _ in range(100):  # random digit strings, mostly failing Luhn
        cases.append("".join(rng.choice("0123456789") for _ in range(13)))
    return cases


def _python_result(value: str) -> dict:
    cleaned = clean_sa_id(value)
    try:
        info = parse_sa_id(value, today=TODAY)
    except SAIdError as exc:
        return {
            "clean": cleaned,
            "valid": False,
            "error": str(exc),
            "dateOfBirth": None,
            "gender": None,
            "isCitizen": None,
        }
    return {
        "clean": cleaned,
        "valid": True,
        "error": None,
        "dateOfBirth": info.date_of_birth.isoformat(),
        "gender": info.gender,
        "isCitizen": info.is_citizen,
    }


@pytest.fixture(scope="module")
def js_results():
    values = _cases()
    payload = json.dumps({"today": [TODAY.year, TODAY.month, TODAY.day], "values": values})
    completed = subprocess.run(
        ["node", "-e", NODE_RUNNER, str(JS_FILE)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return list(zip(values, json.loads(completed.stdout)))


def test_javascript_and_python_agree_on_every_case(js_results):
    assert len(js_results) > 400
    mismatches = []
    for value, js in js_results:
        expected = _python_result(value)
        actual = {key: js[key] for key in expected}
        if actual != expected:
            mismatches.append((value, expected, actual))
    assert not mismatches, f"{len(mismatches)} disagreements, first: {mismatches[0]}"


def test_luhn_helpers_agree(js_results):
    for value, js in js_results:
        cleaned = clean_sa_id(value)
        assert js["luhn"] == luhn_valid(cleaned), value


def test_the_case_set_exercises_both_outcomes(js_results):
    """Guards against a broken generator that makes the parity check pass trivially."""
    valid = sum(1 for _, js in js_results if js["valid"])
    assert valid > 100
    assert len(js_results) - valid > 100
    assert {js["gender"] for _, js in js_results if js["valid"]} == {"Male", "Female"}
    assert {js["isCitizen"] for _, js in js_results if js["valid"]} == {True, False}
