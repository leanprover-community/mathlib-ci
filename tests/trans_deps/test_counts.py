"""End-to-end tests over a fake Lean tree: `main` in process, and the script as a script."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import count_trans_deps
from count_trans_deps import get_imports

LeanTree = Callable[..., str]


def _run(root: str, capsys: pytest.CaptureFixture[str]) -> dict[str, int]:
    """Invoke the script on `root` and return the parsed JSON output."""
    assert count_trans_deps.main([root]) == 0
    return json.loads(capsys.readouterr().out)


class TestCLI:
    def test_emits_one_count_per_module(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = lean_tree({
            "Mathlib.A": "public import Mathlib.B\n",
            "Mathlib.B": "public import Mathlib.C\n",
            "Mathlib.C": "",
        })
        assert _run(root, capsys) == {"Mathlib.A": 2, "Mathlib.B": 1, "Mathlib.C": 0}

    def test_output_is_a_single_json_line(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`import_trans_difference.sh` pipes stdout through `sed`, one record per line."""
        root = lean_tree({"Mathlib.A": "public import Mathlib.B\n", "Mathlib.B": ""})
        assert count_trans_deps.main([root]) == 0
        assert capsys.readouterr().out.count("\n") == 1

    def test_trailing_slash_in_the_directory_argument(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`PR_summary.yml` passes `Mathlib/`, `import_trans_difference.sh` passes `Mathlib`.

        Both must produce the same module names, or the two halves of the PR summary
        would disagree.
        """
        root = lean_tree({"Mathlib.A": "public import Mathlib.B\n", "Mathlib.B": ""})
        assert _run(root, capsys) == _run(root + "/", capsys)

    def test_missing_argument_returns_nonzero(self) -> None:
        assert count_trans_deps.main([]) == 1

    def test_empty_tree(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _run(lean_tree({}), capsys) == {}


class TestRegressionMathlib43438:
    """A trailing comment on an import must not truncate the graph behind it.

    Reported on mathlib4#43438: `Mathlib.Tactic.Echelon.Cert` was summarised as having
    66 transitive imports.  Its first import carries a `-- shake: keep` comment, which
    the old regex swallowed into the module name; the resulting name matched no module,
    so it was counted as a boundary leaf and the entire linear-algebra subtree behind
    it was dropped.  On mathlib master the true count was 1717.
    """

    TREE = {
        "Mathlib.Tactic.Echelon.Cert": (
            "module\n"
            "\n"
            "public import Mathlib.LinearAlgebra.Matrix.Echelon.Decomposition"
            "  -- shake: keep (Qq dependency)\n"
            "public import Mathlib.Tactic.Echelon.Core\n"
            "\n"
            "/-! # Cert -/\n"
        ),
        "Mathlib.LinearAlgebra.Matrix.Echelon.Decomposition": (
            "module\npublic import Mathlib.LinearAlgebra.Matrix.Basic\n"
        ),
        "Mathlib.LinearAlgebra.Matrix.Basic": (
            "module\npublic import Mathlib.Data.Matrix.Defs\n"
        ),
        "Mathlib.Data.Matrix.Defs": "module\n",
        # As in `Mathlib/Tactic/Echelon/Parsing.lean`: a `meta` import *and* a comment.
        "Mathlib.Tactic.Echelon.Core": (
            "module\npublic meta import Mathlib.Util.Qq  -- shake: keep\n"
        ),
        "Mathlib.Util.Qq": "module\n",
    }

    def test_commented_import_keeps_its_subtree(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        counts = _run(lean_tree(self.TREE), capsys)
        # Decomposition, Matrix.Basic, Data.Matrix.Defs, Echelon.Core, Util.Qq.
        # The old regex reported 2: the mangled name plus `Echelon.Core`, whose own
        # `meta` import it never saw at all.
        assert counts["Mathlib.Tactic.Echelon.Cert"] == 5

    def test_meta_import_keeps_its_subtree(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`Echelon.Core`'s only import is a commented `public meta import`."""
        counts = _run(lean_tree(self.TREE), capsys)
        assert counts["Mathlib.Tactic.Echelon.Core"] == 1

    def test_every_extracted_import_resolves_to_a_module(
        self, lean_tree: LeanTree
    ) -> None:
        """No extracted name may carry its trailing comment along.

        This is the invariant that actually broke.  A mangled name matches no module,
        so `get_transitive_imports` treats it as a boundary leaf and stops there — the
        counts stay plausible-looking while the subtree behind it disappears.
        """
        root = lean_tree(self.TREE)
        file_imports = get_imports(root)
        refs = {ref for imports in file_imports.values() for ref in imports}
        unresolved = sorted(refs - set(file_imports))
        assert not unresolved, f"imports resolving to no module: {unresolved}"


class TestExecutedAsAScript:
    """CI runs this file as a script; these tests import it.

    `main` is only reachable through the `if __name__ == '__main__'` guard, so the
    guard needs coverage of its own: drop it and the script prints nothing and exits 0,
    which every import-based test here would happily continue to pass.
    """

    def test_prints_counts_and_exits_zero(
        self, lean_tree: LeanTree, script_path: Path
    ) -> None:
        root = lean_tree({"Mathlib.A": "public import Mathlib.B\n", "Mathlib.B": ""})
        proc = subprocess.run(
            [sys.executable, str(script_path), root], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) == {"Mathlib.A": 1, "Mathlib.B": 0}

    def test_missing_argument_exits_nonzero(self, script_path: Path) -> None:
        proc = subprocess.run(
            [sys.executable, str(script_path)], capture_output=True, text=True
        )
        assert proc.returncode == 1
        assert "directory name" in proc.stdout
