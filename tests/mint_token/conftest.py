"""Put the azure-create-github-app-token action's source dir on sys.path for import."""

from __future__ import annotations

import sys
from pathlib import Path

_ACTION = Path(__file__).resolve().parents[2] / ".github" / "actions" / "azure-create-github-app-token"

sys.path.insert(0, str(_ACTION))
