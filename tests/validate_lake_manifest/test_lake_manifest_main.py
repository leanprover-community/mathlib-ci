"""The level and the annotation of each finding, in `__main__.py`."""

from __future__ import annotations

import pytest

from lake_manifest.__main__ import ADMIN_NOTE, OVERRIDE_NOTE, STRICT_NOTE, annotate, command
from lake_manifest.validate import FIELD, OLDER_PIN, PIN, Finding

PIN_FINDING = Finding(PIN, "t", "problem", ("Hint: fix.",))
OLDER_FINDING = Finding(OLDER_PIN, "t", "problem", ("Hint: fix.",))
FIELD_FINDING = Finding(FIELD, "t", "problem", ("Note: rule.", "Hint: fix."))


@pytest.mark.parametrize("finding, strict, allow_older_pins, expected", [
    (FIELD_FINDING, False, False, ("error", "problem\nNote: rule.\nHint: fix.")),
    (PIN_FINDING, False, False, ("warning", f"problem\n{STRICT_NOTE}\nHint: fix.")),
    (PIN_FINDING, True, True, ("error", f"problem\n{STRICT_NOTE}\nHint: fix.")),
    (OLDER_FINDING, True, False, ("error", f"problem\n{STRICT_NOTE}\n{ADMIN_NOTE}\nHint: fix.")),
    (OLDER_FINDING, False, False, ("warning", f"problem\n{STRICT_NOTE}\n{ADMIN_NOTE}\nHint: fix.")),
    (OLDER_FINDING, True, True, ("warning", f"problem\n{OVERRIDE_NOTE}")),
], ids=["field", "pin", "pin-strict-override", "older-strict", "older", "older-override"])
def test_annotate(finding, strict, allow_older_pins, expected):
    assert annotate(finding, strict, allow_older_pins) == expected


def test_command_escapes_properties_and_data():
    assert command("error", "a: b, c", "100%\nline\r") == "::error file=lake-manifest.json,title=a%3A b%2C c::100%25%0Aline%0D"
