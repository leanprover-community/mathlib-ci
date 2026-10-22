"""Tests for the module-system import count.

Lean loads, for a `module` file, its direct imports and the closure of their `public`
imports.  A private `import` in an imported file is not loaded.  `import all X` also loads
the direct imports of `X`.  `meta` does not change which modules are loaded.  A file
without a `module` header loads its whole transitive closure.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

import count_trans_deps
from count_trans_deps import get_import_specs

LeanTree = Callable[..., str]


def _run(root: str, capsys: pytest.CaptureFixture[str]) -> dict[str, int]:
    assert count_trans_deps.main([root]) == 0
    return json.loads(capsys.readouterr().out)


class TestModuleFiles:
    def test_private_import_of_an_import_is_not_loaded(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A → B publicly, B → C privately: A loads B only."""
        root = lean_tree({
            "Mathlib.A": "module\npublic import Mathlib.B\n",
            "Mathlib.B": "module\nimport Mathlib.C\n",
            "Mathlib.C": "module\n",
        })
        assert _run(root, capsys) == {"Mathlib.A": 1, "Mathlib.B": 1, "Mathlib.C": 0}

    def test_public_imports_are_loaded_transitively(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = lean_tree({
            "Mathlib.A": "module\npublic import Mathlib.B\n",
            "Mathlib.B": "module\npublic import Mathlib.C\n",
            "Mathlib.C": "module\npublic import Mathlib.D\n",
            "Mathlib.D": "module\n",
        })
        assert _run(root, capsys)["Mathlib.A"] == 3

    def test_own_private_import_loads_the_public_closure_of_its_target(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A → B privately, B → C publicly, C → D privately: A loads B and C."""
        root = lean_tree({
            "Mathlib.A": "module\nimport Mathlib.B\n",
            "Mathlib.B": "module\npublic import Mathlib.C\n",
            "Mathlib.C": "module\nimport Mathlib.D\n",
            "Mathlib.D": "module\n",
        })
        assert _run(root, capsys)["Mathlib.A"] == 2

    def test_import_all_loads_the_private_imports_of_its_target(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A → B with `import all`, B → C privately, C → D publicly: A loads B, C and D."""
        root = lean_tree({
            "Mathlib.A": "module\nimport all Mathlib.B\n",
            "Mathlib.B": "module\nimport Mathlib.C\n",
            "Mathlib.C": "module\npublic import Mathlib.D\nimport Mathlib.E\n",
            "Mathlib.D": "module\n",
            "Mathlib.E": "module\n",
        })
        assert _run(root, capsys)["Mathlib.A"] == 3

    def test_import_all_inside_a_public_closure_acts_as_a_public_import(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """B's `public import all C` exposes C to A, but not C's private imports."""
        root = lean_tree({
            "Mathlib.A": "module\npublic import Mathlib.B\n",
            "Mathlib.B": "module\npublic import all Mathlib.C\n",
            "Mathlib.C": "module\nimport Mathlib.D\n",
            "Mathlib.D": "module\n",
        })
        assert _run(root, capsys)["Mathlib.A"] == 2

    def test_meta_does_not_change_what_is_loaded(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = lean_tree({
            "Mathlib.A": "module\nmeta import Mathlib.B\n",
            "Mathlib.B": "module\npublic meta import Mathlib.C\nmeta import Mathlib.D\n",
            "Mathlib.C": "module\n",
            "Mathlib.D": "module\n",
        })
        assert _run(root, capsys)["Mathlib.A"] == 2

    def test_boundary_leaf_counts_once(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Modules outside the walked tree count once and are not descended."""
        root = lean_tree({
            "Mathlib.A": "module\nimport Lean.Elab.Command\npublic import Mathlib.B\n",
            "Mathlib.B": "module\npublic import Lean.Elab.Command\n",
        })
        assert _run(root, capsys)["Mathlib.A"] == 2

    def test_module_header_with_trailing_comment(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Mathlib writes `module -- shake: keep-all` in many files."""
        root = lean_tree({
            "Mathlib.A": "module -- shake: keep-all\npublic import Mathlib.B\n",
            "Mathlib.B": "module  -- shake: keep-downstream\nimport Mathlib.C\n",
            "Mathlib.C": "module\n",
        })
        assert _run(root, capsys)["Mathlib.A"] == 1


class TestFilesWithoutModuleHeader:
    def test_root_without_module_header_loads_everything(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A file without `module` ignores visibility and loads the full closure."""
        root = lean_tree({
            "Mathlib.A": "import Mathlib.B\n",
            "Mathlib.B": "module\nimport Mathlib.C\n",
            "Mathlib.C": "module\n",
        })
        assert _run(root, capsys)["Mathlib.A"] == 2

    def test_imported_file_without_module_header_exports_all_its_imports(
        self, lean_tree: LeanTree, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = lean_tree({
            "Mathlib.A": "module\nimport Mathlib.B\n",
            "Mathlib.B": "import Mathlib.C\n",
            "Mathlib.C": "import Mathlib.D\n",
            "Mathlib.D": "",
        })
        assert _run(root, capsys)["Mathlib.A"] == 3


class TestGetImportSpecs:
    def test_flags_and_module_header(self, lean_tree: LeanTree) -> None:
        root = lean_tree({
            "Mathlib.Foo": (
                "module -- shake: keep-all\n"
                "\n"
                "public import Mathlib.A  -- shake: keep\n"
                "public meta import Mathlib.B\n"
                "meta import Mathlib.C\n"
                "import all Mathlib.D\n"
                "public import all Mathlib.E\n"
                "import Mathlib.F\n"
                "\n"
                "/-! # Foo -/\n"
            ),
            "Mathlib.Bar": "import Mathlib.A\n",
        })
        specs = get_import_specs(root)
        assert specs["Mathlib.Foo"] == (True, [
            ("Mathlib.A", True, False),
            ("Mathlib.B", True, False),
            ("Mathlib.C", False, False),
            ("Mathlib.D", False, True),
            ("Mathlib.E", True, True),
            ("Mathlib.F", False, False),
        ])
        assert specs["Mathlib.Bar"] == (False, [("Mathlib.A", False, False)])

    def test_module_keyword_must_be_a_whole_word(self, lean_tree: LeanTree) -> None:
        root = lean_tree({"Mathlib.Foo": "moduleX\nimport Mathlib.A\n"})
        assert get_import_specs(root)["Mathlib.Foo"][0] is False
