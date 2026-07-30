"""Drive verify_commits_summary.sh as a subprocess, feeding JSON on stdin.

The summary script is bash, so these tests exercise the real script exactly as
CI runs it (rendering, section ordering, size capping, code-fence hardening)
rather than reimplementing its logic in Python.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "verification"
    / "verify_commits_summary.sh"
)


def _render(doc, *, repo="leanprover-community/mathlib4", pr=1, env_extra=None):
    """Run the summary script and return its stdout (the rendered comment).

    `doc` may be a dict (JSON-encoded before piping) or a raw string (piped
    verbatim, e.g. to exercise the invalid-JSON path).
    """
    payload = doc if isinstance(doc, str) else json.dumps(doc)
    env = os.environ.copy()
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})
    proc = subprocess.run(
        ["bash", str(SCRIPT), repo, str(pr)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"script exited {proc.returncode}: {proc.stderr}"
    return proc.stdout


@pytest.fixture
def render():
    return _render
