"""Tests for declsDiff.compute_diff / read_decls."""

from __future__ import annotations

from pathlib import Path

import pytest

from declsDiff import compute_diff, read_decls


class TestComputeDiff:
    def test_empty_inputs(self) -> None:
        """No names on either side → no diff entries."""
        assert compute_diff(set(), set()) == []

    def test_identical_inputs(self) -> None:
        """Identical sets → empty diff regardless of size."""
        s = {"A", "B", "C"}
        assert compute_diff(s, s) == []

    def test_only_additions(self) -> None:
        """Names only in `new` get a `+` sign; order is by name."""
        assert compute_diff(set(), {"B", "A"}) == [("+", "A"), ("+", "B")]

    def test_only_removals(self) -> None:
        """Names only in `ref` get a `-` sign."""
        assert compute_diff({"B", "A"}, set()) == [("-", "A"), ("-", "B")]

    def test_mixed_additions_and_removals(self) -> None:
        """Adds and removes are merged and sorted by name only — sign is not a tiebreak."""
        ref = {"A", "B", "C"}
        new = {"A", "C", "D"}
        assert compute_diff(ref, new) == [("-", "B"), ("+", "D")]

    def test_sort_is_by_name_only(self) -> None:
        """`+B` precedes `-C` because the sort key is the name, not the sign."""
        ref = {"C"}
        new = {"B"}
        assert compute_diff(ref, new) == [("+", "B"), ("-", "C")]


class TestReadDecls:
    def test_basic(self, tmp_path: Path) -> None:
        """One name per line, blank lines dropped, duplicates collapsed."""
        f = tmp_path / "decls.txt"
        f.write_text("A\nB\n\nA\nC\n")
        assert read_decls(f) == {"A", "B", "C"}

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty file → empty set."""
        f = tmp_path / "decls.txt"
        f.write_text("")
        assert read_decls(f) == set()

    def test_only_blank_lines(self, tmp_path: Path) -> None:
        """A file containing only blank lines yields no names."""
        f = tmp_path / "decls.txt"
        f.write_text("\n\n\n")
        assert read_decls(f) == set()

    def test_no_trailing_newline(self, tmp_path: Path) -> None:
        """The last name is captured even without a trailing newline."""
        f = tmp_path / "decls.txt"
        f.write_text("A\nB")
        assert read_decls(f) == {"A", "B"}
