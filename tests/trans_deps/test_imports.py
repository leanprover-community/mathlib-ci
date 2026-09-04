"""Tests for the import-line regex and `get_imports`.

Lean's import grammar is `(public)? (meta)? import (all)? <module>` (see
`Mathlib/Tactic/Linter/Header.lean`), and mathlib annotates many imports with a
trailing `-- shake: keep` comment.  Every one of those shapes has to yield the bare
module name: a `ref` that carries a comment along with it does not match any module
in the graph, so it is silently treated as a boundary leaf and the whole subtree
behind it vanishes from the count.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from count_trans_deps import IMPORT_RE, get_imports

LeanTree = Callable[..., str]


def _ref(line: str) -> str | None:
    """The module name the script extracts from `line`, or `None` if it is not an import."""
    match = IMPORT_RE.match(line)
    return None if match is None else match.groupdict()["ref"]


class TestKeywordForms:
    def test_plain_import(self) -> None:
        assert _ref("import Mathlib.Bar\n") == "Mathlib.Bar"

    def test_public_import(self) -> None:
        assert _ref("public import Mathlib.Bar\n") == "Mathlib.Bar"

    def test_meta_import(self) -> None:
        """`meta import` is a dependency like any other; it must not be skipped."""
        assert _ref("meta import Mathlib.Bar\n") == "Mathlib.Bar"

    def test_public_meta_import(self) -> None:
        """The overwhelmingly common form in mathlib after the module-system port."""
        assert _ref("public meta import Mathlib.Bar\n") == "Mathlib.Bar"

    def test_import_all(self) -> None:
        """`all` is a modifier, not part of the module name."""
        assert _ref("import all Mathlib.Bar\n") == "Mathlib.Bar"

    def test_public_meta_import_all(self) -> None:
        assert _ref("public meta import all Mathlib.Bar\n") == "Mathlib.Bar"

    def test_extra_whitespace_between_keywords(self) -> None:
        assert _ref("public  meta   import   all   Mathlib.Bar\n") == "Mathlib.Bar"

    def test_tab_separator(self) -> None:
        assert _ref("public\timport\tMathlib.Bar\n") == "Mathlib.Bar"


class TestTrailingText:
    def test_line_comment(self) -> None:
        """The bug behind mathlib4#43438: `-- shake: keep` must not join the module name."""
        line = ("public import Mathlib.LinearAlgebra.Matrix.Echelon.Decomposition"
                "  -- shake: keep (Qq dependency)\n")
        assert _ref(line) == "Mathlib.LinearAlgebra.Matrix.Echelon.Decomposition"

    def test_line_comment_without_space(self) -> None:
        """`Mathlib/Tactic/Lemma.lean` writes `--shake: keep` with no space."""
        assert _ref("public import Mathlib.Bar  --shake: keep\n") == "Mathlib.Bar"

    def test_block_comment(self) -> None:
        assert _ref("public import Mathlib.Bar /- keep -/\n") == "Mathlib.Bar"

    def test_meta_import_with_comment(self) -> None:
        """`Mathlib/Tactic/Echelon/Parsing.lean` combines both; it counted 0 imports."""
        line = "public meta import Mathlib.LinearAlgebra.Matrix.Notation -- shake: keep\n"
        assert _ref(line) == "Mathlib.LinearAlgebra.Matrix.Notation"

    def test_trailing_whitespace(self) -> None:
        assert _ref("public import Mathlib.Bar   \n") == "Mathlib.Bar"

    def test_carriage_return(self) -> None:
        """A CRLF line ending must not become part of the module name."""
        assert _ref("public import Mathlib.Bar\r\n") == "Mathlib.Bar"

    def test_no_trailing_newline(self) -> None:
        assert _ref("public import Mathlib.Bar") == "Mathlib.Bar"


class TestNonImports:
    def test_commented_out_import(self) -> None:
        assert _ref("-- import Mathlib.Bar\n") is None

    def test_indented_import(self) -> None:
        """Header imports start at column 0; an indented one is inside prose or code."""
        assert _ref("  import Mathlib.Bar\n") is None

    def test_import_without_module(self) -> None:
        assert _ref("import\n") is None

    def test_keyword_must_be_a_whole_word(self) -> None:
        assert _ref("importMathlib.Bar\n") is None
        assert _ref("imports Mathlib.Bar\n") is None

    def test_module_and_prelude_headers(self) -> None:
        assert _ref("module\n") is None
        assert _ref("prelude\n") is None

    def test_deprecated_module_command(self) -> None:
        assert _ref('deprecated_module "gone" (since := "2026-06-19")\n') is None


class TestGetImports:
    def test_reads_every_keyword_form_from_a_file(self, lean_tree: LeanTree) -> None:
        """A single header exercising all the forms mathlib actually uses."""
        root = lean_tree({
            "Mathlib.Foo": (
                "module\n"
                "\n"
                "public import Mathlib.A  -- shake: keep (Qq dependency)\n"
                "public meta import Mathlib.B\n"
                "meta import Mathlib.C\n"
                "import all Mathlib.D\n"
                "import Mathlib.E\n"
                "\n"
                "/-!\n"
                "# Foo\n"
                "-/\n"
            ),
        })
        assert get_imports(root)["Mathlib.Foo"] == [
            "Mathlib.A", "Mathlib.B", "Mathlib.C", "Mathlib.D", "Mathlib.E",
        ]

    def test_stops_at_the_module_docstring(self, lean_tree: LeanTree) -> None:
        """Imports quoted in the docstring are examples, not dependencies.

        `Mathlib/Tactic/MinImports.lean` has a fenced `import` block in its docstring,
        and several files write prose like "... in the import hierarchy."
        """
        root = lean_tree({
            "Mathlib.Foo": (
                "module\n"
                "public import Mathlib.A\n"
                "\n"
                "/-! # Foo\n"
                "This sits low in the import hierarchy.\n"
                "```lean\n"
                "import Mathlib.NotADependency\n"
                "```\n"
                "-/\n"
            ),
        })
        assert get_imports(root)["Mathlib.Foo"] == ["Mathlib.A"]

    def test_file_without_imports(self, lean_tree: LeanTree) -> None:
        """A `deprecated_module` shim imports nothing but is still a key."""
        root = lean_tree({
            "Mathlib.Foo": (
                "module\n"
                "\n"
                "/-! # Foo\n"
                "Deprecated.\n"
                "-/\n"
                "\n"
                'deprecated_module "use Mathlib.Bar instead" (since := "2026-06-19")\n'
            ),
        })
        assert get_imports(root) == {"Mathlib.Foo": []}

    def test_only_lean_files_are_read(self, lean_tree: LeanTree) -> None:
        """Non-`.lean` files in the tree are ignored."""
        root = lean_tree({"Mathlib.Foo": "public import Mathlib.A\n"})
        (Path(root) / "notes.md").write_text("import Mathlib.B\n")
        assert get_imports(root) == {"Mathlib.Foo": ["Mathlib.A"]}

    def test_module_names_come_from_the_path(self, lean_tree: LeanTree) -> None:
        """Nested directories become dotted components of the module name."""
        root = lean_tree({
            "Mathlib.Tactic.Echelon.Cert": "public import Mathlib.A\n",
            "Mathlib.A": "",
        })
        assert set(get_imports(root)) == {"Mathlib.Tactic.Echelon.Cert", "Mathlib.A"}
