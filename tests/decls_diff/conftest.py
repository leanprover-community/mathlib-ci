"""Put the script's source directory on sys.path so tests can import `declsDiff`."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "pr_summary"

sys.path.insert(0, str(_SCRIPT_DIR))
