"""Put the script's source directory on sys.path so tests can import `zulip_build_report`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "reporting"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(_SCRIPT_DIR))


@pytest.fixture
def mathlib_log() -> Path:
    """Trimmed real log of mathlib4 weekly run 33358987484 (792 warnings, 36 info)."""
    return FIXTURES / "mathlib_weekly.log"


@pytest.fixture
def cslib_log() -> Path:
    """Trimmed real log of cslib weekly run 33359064105 (3 info messages)."""
    return FIXTURES / "cslib_weekly.log"
