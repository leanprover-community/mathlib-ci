"""Put both composite-action source dirs on sys.path so tests can import them."""

from __future__ import annotations

import sys
from pathlib import Path

_ACTIONS = Path(__file__).resolve().parents[2] / ".github" / "actions"

sys.path.insert(0, str(_ACTIONS / "check-downstream-validate"))
sys.path.insert(0, str(_ACTIONS / "check-downstream-ack"))
