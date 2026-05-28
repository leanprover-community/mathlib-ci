"""Tests for grammar.parse_directive / parse_entry / serialize."""

from __future__ import annotations

import pytest

from grammar import (
    MERGE_FLAG,
    MODE_LKG,
    MODE_MERGE,
    Entry,
    GrammarError,
    parse_directive,
    parse_entry,
    serialize,
)


class TestParseEntry:
    def test_bare_name_defaults_to_lkg_no_rev(self) -> None:
        """A bare `<name>` defaults to LKG mode with no rev."""
        assert parse_entry("FLT") == Entry("FLT", None, MODE_LKG)

    def test_rev_suffix_captured(self) -> None:
        """`<name>@<rev>` splits the rev off the bare token."""
        assert parse_entry("FLT@v1.2.3") == Entry("FLT", "v1.2.3", MODE_LKG)

    def test_merge_branch_flag_flips_mode(self) -> None:
        """`--merge-branch` flips that entry from the default LKG mode to merge mode."""
        assert parse_entry("FLT --merge-branch") == Entry("FLT", None, MODE_MERGE)

    def test_rev_and_flag_combined(self) -> None:
        """Rev and flag combine — the grammar's most expressive entry form."""
        assert parse_entry("FLT@v1.2.3 --merge-branch") == Entry("FLT", "v1.2.3", MODE_MERGE)

    def test_slug_form_accepted_as_bare_token(self) -> None:
        """`owner/repo` slugs are valid bare tokens; resolution happens downstream."""
        assert parse_entry("leanprover-community/FLT@v1 --merge-branch") == Entry(
            "leanprover-community/FLT", "v1", MODE_MERGE
        )

    def test_unknown_flag_raises_with_hint(self) -> None:
        """Any flag but `--merge-branch` raises, with a hint naming the valid set."""
        with pytest.raises(GrammarError) as exc:
            parse_entry("FLT --bogus")
        assert "--bogus" in exc.value.message
        assert MERGE_FLAG in exc.value.hint

    def test_empty_name_raises(self) -> None:
        """Without a name there's nothing for the dispatched workflow to resolve."""
        with pytest.raises(GrammarError, match="empty downstream name"):
            parse_entry("@v1")

    def test_empty_rev_after_at_raises(self) -> None:
        """Trailing `@` is almost certainly a typo; fail loudly, don't treat as no-rev."""
        with pytest.raises(GrammarError, match="empty rev"):
            parse_entry("FLT@")


class TestParseDirective:
    def test_multiple_entries_in_input_order(self) -> None:
        """Comma-separated entries are returned in input order."""
        assert parse_directive("FLT, Toric --merge-branch, carleson@v1") == [
            Entry("FLT", None, MODE_LKG),
            Entry("Toric", None, MODE_MERGE),
            Entry("carleson", "v1", MODE_LKG),
        ]

    def test_whitespace_around_entries_tolerated(self) -> None:
        """Whitespace around entries and separators is trimmed."""
        assert parse_directive("  FLT  ,   Toric --merge-branch  ") == [
            Entry("FLT", None, MODE_LKG),
            Entry("Toric", None, MODE_MERGE),
        ]

    def test_empty_body_raises_with_usage_hint(self) -> None:
        """An empty body raises with the usage line as a hint."""
        with pytest.raises(GrammarError) as exc:
            parse_directive("")
        assert "no downstream entries" in exc.value.message
        assert "!downstream-check" in exc.value.hint

    def test_only_commas_and_whitespace_raises(self) -> None:
        """A body of only commas/whitespace is rejected like an empty one."""
        with pytest.raises(GrammarError, match="no downstream entries"):
            parse_directive("  ,   ,  ")

    def test_dedup_keeps_distinct_collapses_exact(self) -> None:
        """Entries equal on (name, rev, mode) collapse; differing fields survive."""
        assert parse_directive("FLT, FLT, FLT@main, FLT@main, FLT --merge-branch") == [
            Entry("FLT", None, MODE_LKG),
            Entry("FLT", "main", MODE_LKG),
            Entry("FLT", None, MODE_MERGE),
        ]

    def test_first_grammar_error_short_circuits(self) -> None:
        """Report one issue at a time so the PR comment stays focused; user re-triggers."""
        with pytest.raises(GrammarError, match="--bogus"):
            parse_directive("FLT, Toric --bogus, carleson --also-bogus")


class TestSerialize:
    def test_merge_mode_appends_flag(self) -> None:
        """Merge mode serializes back with the `--merge-branch` flag appended."""
        assert serialize(Entry("FLT", None, MODE_MERGE)) == "FLT --merge-branch"

    def test_rev_and_merge_flag_combined(self) -> None:
        """Rev and merge flag serialize together."""
        assert serialize(Entry("FLT", "v1", MODE_MERGE)) == "FLT@v1 --merge-branch"

    def test_roundtrip_through_parse(self) -> None:
        """The dispatched workflow re-parses `resolved_names`; pin the round-trip."""
        for form in [
            "FLT",
            "FLT@v1.2.3",
            "FLT --merge-branch",
            "FLT@v1.2.3 --merge-branch",
            "leanprover-community/FLT@main --merge-branch",
        ]:
            entry = parse_entry(form)
            assert parse_entry(serialize(entry)) == entry
