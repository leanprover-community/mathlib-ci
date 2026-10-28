"""Put the scripts' source directory on sys.path so tests can import `predict_cascade` and `route_build`."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "route"

sys.path.insert(0, str(_SCRIPT_DIR))
