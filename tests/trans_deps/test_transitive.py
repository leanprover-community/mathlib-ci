"""Tests for `get_transitive_imports` / `count_transitive_imports`.

The counts are set sizes, so a module reachable by several paths is counted once.
Modules outside the tree that was walked (`Lean.*`, `Batteries.*`, ...) are boundary
leaves: they count once each as a dependency, but the search does not descend into
them, since their own imports are not known.  This matches what the Lean-side
`dumpReasonableDecls.lean` produces from `env.importGraph`.
"""

from __future__ import annotations

from count_trans_deps import count_transitive_imports, get_transitive_imports


def _counts(file_imports: dict[str, list[str]]) -> dict[str, int]:
    return count_transitive_imports(get_transitive_imports(file_imports))


class TestTransitiveImports:
    def test_no_imports(self) -> None:
        assert _counts({"A": []}) == {"A": 0}

    def test_chain(self) -> None:
        """A → B → C: A depends on both B and C."""
        assert _counts({"A": ["B"], "B": ["C"], "C": []}) == {"A": 2, "B": 1, "C": 0}

    def test_diamond_counts_the_shared_module_once(self) -> None:
        """A → B, C → D: D is reachable twice but contributes 1."""
        counts = _counts({"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []})
        assert counts == {"A": 3, "B": 1, "C": 1, "D": 0}

    def test_direct_and_indirect_import_of_the_same_module(self) -> None:
        """A → B and A → C → B: B is not double counted."""
        assert _counts({"A": ["B", "C"], "C": ["B"], "B": []}) == {"A": 2, "C": 1, "B": 0}

    def test_repeated_import_line(self) -> None:
        """The same module listed twice in one header contributes 1."""
        assert _counts({"A": ["B", "B"], "B": []}) == {"A": 1, "B": 0}

    def test_boundary_leaf_counts_once_and_is_not_descended(self) -> None:
        """`Lean.Elab.Command` is outside the walked tree: it has no known imports."""
        counts = _counts({"A": ["Lean.Elab.Command", "B"], "B": ["Lean.Elab.Command"]})
        assert counts["A"] == 2
        assert counts["B"] == 1
        assert "Lean.Elab.Command" not in counts

    def test_result_is_independent_of_traversal_order(self) -> None:
        """`os.walk` order is arbitrary, so the counts must not depend on it."""
        graph = {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": ["E"], "E": []}
        forward = _counts(graph)
        reverse = _counts({k: graph[k] for k in reversed(list(graph))})
        assert forward == reverse
        assert forward == {"A": 4, "B": 2, "C": 2, "D": 1, "E": 0}

    def test_keys_cover_every_input_module(self) -> None:
        """Every module walked gets a count, even one nothing imports."""
        assert set(_counts({"A": ["B"], "B": [], "Orphan": []})) == {"A", "B", "Orphan"}
