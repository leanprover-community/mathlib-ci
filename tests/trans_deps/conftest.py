"""Load `count-trans-deps.py` and provide a fake-Lean-tree fixture.

The script's filename is not a valid Python identifier — and it is baked into
mathlib4's `PR_summary.yml` and into `import_trans_difference.sh`, so it cannot be
renamed unilaterally.  It is therefore loaded by path rather than by putting its
directory on `sys.path`, as the sibling suites here do.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "pr_summary" / "count-trans-deps.py"
)

_spec = importlib.util.spec_from_file_location("count_trans_deps", _SCRIPT)
assert _spec is not None and _spec.loader is not None
count_trans_deps = importlib.util.module_from_spec(_spec)
sys.modules["count_trans_deps"] = count_trans_deps
_spec.loader.exec_module(count_trans_deps)


@pytest.fixture
def script_path() -> Path:
    """The script itself, for driving it the way CI does — as a script, not an import."""
    return _SCRIPT


@pytest.fixture
def lean_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Write a fake Lean source tree, `cd` into it, and return the root directory name.

    `files` maps a module name to that module's file contents, e.g.
    `{"Mathlib.Tactic.Foo": "module\\npublic import Mathlib.Bar\\n"}`.  Module names
    are turned back into the paths the real layout uses.

    The script derives module names from the path it is handed, so the tests run
    with the tree root as the working directory and pass a relative directory —
    exactly how `PR_summary.yml` and `import_trans_difference.sh` invoke it.
    """

    def _build(files: dict[str, str], root: str = "Mathlib") -> str:
        for module, text in files.items():
            path = tmp_path / (module.replace(".", "/") + ".lean")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        (tmp_path / root).mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(tmp_path)
        return root

    return _build
