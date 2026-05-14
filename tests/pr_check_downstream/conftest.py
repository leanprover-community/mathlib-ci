"""Test-time sys.path bootstrap for the check-downstream composite actions.

The action source lives under ``.github/actions/<name>/`` so each action
directory is self-contained for ``actions/checkout`` to grab.  Tests
live here so they're easy to discover with a single ``pytest tests``
invocation; both action directories go onto ``sys.path`` so test
modules can ``from grammar import …`` etc. without packaging tricks.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ACTIONS = _REPO_ROOT / ".github" / "actions"

# Order matters: validate-action source comes first so its `grammar`
# / `error_comment` modules win the import-name race over anything
# else that happens to define the same names.
sys.path.insert(0, str(_ACTIONS / "check-downstream-validate"))
sys.path.insert(0, str(_ACTIONS / "check-downstream-ack"))
