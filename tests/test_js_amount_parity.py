"""App.parseAmount (app/static/js/main.js) and parse_amount (app/utils/money.py) must read what a student types the same way.

If they drift, the form says "R1 500" is fine and the server then refuses it (or the other way round).
The browser code is run in node with a tiny stand-in for ``window`` and ``document``.
"""

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from app.utils.money import parse_amount

JS_FILE = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "main.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

NODE_RUNNER = r"""
const vm = require("vm");
const source = require("fs").readFileSync(process.argv[1], "utf8");
const windowStub = { APP_CONFIG: {}, matchMedia: () => ({ matches: false }) };
const documentStub = { addEventListener() {}, querySelector() { return null; }, documentElement: {} };
vm.runInNewContext(source, { window: windowStub, document: documentStub, getComputedStyle: () => ({ getPropertyValue: () => "" }) });
const values = JSON.parse(require("fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(values.map((v) => windowStub.App.parseAmount(v))));
"""

CASES = [
    "0",
    "1",
    "10",
    "29.99",
    "29,99",
    "29.9",
    "29,9",
    "0.5",
    "0,5",
    "00.50",
    "1500",
    "1 500",
    "1\u00a0500",
    "R1 500",
    "r1500",
    "R 1 500",
    "R1,500",
    "1,500",
    "1,500.50",
    "1,500,000",
    "1,500,000.99",
    "12,345,678.9",
    "1,50",
    "1,5",
    "1,50,000",
    "1,5000",
    "1.500",
    "1.500,50",
    "1 500,50",
    "1500,50",
    "R",
    "",
    " ",
    "abc",
    "R abc",
    "1e3",
    "1E3",
    "-5",
    "+5",
    "5-",
    "--5",
    "5.",
    ".5",
    ",5",
    "5,",
    "1.2.3",
    "1,2,3",
    "1,000,00",
    "1,00",
    "12.345",
    "12,345",
    "١٢٣",
    "R١٢٣",
    "1\u2009500",
    "1\u202f500",
    "١,٥٠٠",
    "0x10",
    "Infinity",
    "NaN",
    "1_000",
    "1 500 000",
    "R1 500 000.99",
    "9999999999",
    "99999999.99",
    "R0",
    "R0.00",
    "R.50",
    "RR5",
    "R-5",
    "R+5",
    "1 ,500",
    "1, 500",
    None,
]


@pytest.fixture(scope="module")
def js_results():
    completed = subprocess.run(
        ["node", "-e", NODE_RUNNER, str(JS_FILE)],
        input=json.dumps(CASES),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return list(zip(CASES, json.loads(completed.stdout)))


def _python(value):
    parsed = parse_amount(value)
    return None if parsed is None else parsed


def test_javascript_and_python_read_every_case_the_same(js_results):
    mismatches = []
    for value, js in js_results:
        python = _python(value)
        same = (python is None and js is None) or (
            python is not None and js is not None and Decimal(str(js)).quantize(Decimal("0.01")) == python
        )
        if not same:
            mismatches.append((value, python, js))
    assert not mismatches, f"{len(mismatches)} disagreements: {mismatches}"


def test_the_cases_cover_accepted_and_refused_input(js_results):
    accepted = [value for value, js in js_results if js is not None]
    assert len(accepted) > 20 and len(js_results) - len(accepted) > 20
