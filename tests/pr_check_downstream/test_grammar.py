"""
Tests for: grammar.parse_directive / parse_entry / serialize

Coverage scope:
    - Every shape of valid entry: bare name, `@<rev>`, `--merge-branch`,
      both combined, slug form (a name that contains `/`).
    - Every distinct GrammarError variant: empty body, empty name,
      empty rev after `@`, unknown flag.
    - Dedup behaviour: exact-duplicate entries collapse; entries that
      differ in rev or mode survive.
    - `serialize` round-trips an Entry back to the directive form (the
      `resolved_names` output passed to the dispatched workflow).

Out of scope:
    - The HTTP layer in `error_comment` (rendering of the body is
      covered separately in `test_error_comment.py`).
"""

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
    """`parse_entry` covers the single-entry grammar."""

    def test_bare_name_defaults_to_lkg_no_rev(self) -> None:
        """A bare `<name>` defaults to LKG mode with `rev=None`."""
        # Arrange / Act / Assert
        assert parse_entry("FLT") == Entry("FLT", None, MODE_LKG)

    def test_rev_suffix_captured(self) -> None:
        """`<name>@<rev>` splits the rev off the bare token."""
        # Arrange / Act / Assert
        assert parse_entry("FLT@v1.2.3") == Entry("FLT", "v1.2.3", MODE_LKG)

    def test_merge_branch_flag_flips_mode(self) -> None:
        """The `--merge-branch` flag flips that entry to merge mode."""
        # Arrange / Act / Assert
        assert parse_entry("FLT --merge-branch") == Entry("FLT", None, MODE_MERGE)

    def test_rev_and_flag_combined(self) -> None:
        """Rev + flag work together (the grammar's most expressive form)."""
        # Arrange / Act / Assert
        assert parse_entry("FLT@v1.2.3 --merge-branch") == Entry(
            "FLT", "v1.2.3", MODE_MERGE
        )

    def test_slug_form_accepted_as_bare_token(self) -> None:
        """`owner/repo` slugs are valid bare tokens (resolution happens downstream).

        The validate action is grammar-only; the dispatched workflow's
        ``build_matrix.py`` is what decides whether a slug matches an
        inventory row.
        """
        # Arrange / Act / Assert
        assert parse_entry("leanprover-community/FLT") == Entry(
            "leanprover-community/FLT", None, MODE_LKG
        )
        assert parse_entry("leanprover-community/FLT@v1 --merge-branch") == Entry(
            "leanprover-community/FLT", "v1", MODE_MERGE
        )

    def test_unknown_flag_raises_with_hint(self) -> None:
        """Any flag other than `--merge-branch` raises with a hint naming the valid set.

        The hint lands on a second line of the error comment so the
        user sees both what was wrong AND what would have been right.
        """
        # Arrange / Act
        with pytest.raises(GrammarError) as exc:
            parse_entry("FLT --bogus")
        # Assert
        assert "--bogus" in exc.value.message
        assert MERGE_FLAG in exc.value.hint

    def test_empty_name_raises(self) -> None:
        """`@v1` (a leading `@` with no bare token) is rejected.

        Without a name, there's nothing for the dispatched workflow to
        resolve against the inventory.
        """
        # Arrange / Act / Assert
        with pytest.raises(GrammarError, match="empty downstream name"):
            parse_entry("@v1")

    def test_empty_rev_after_at_raises(self) -> None:
        """`FLT@` (with `@` and nothing after) is rejected.

        Trailing `@` is almost certainly a typo; failing loudly here
        is better than silently treating it as "no rev".
        """
        # Arrange / Act / Assert
        with pytest.raises(GrammarError, match="empty rev"):
            parse_entry("FLT@")


class TestParseDirective:
    """`parse_directive` parses the full comma-separated entry list."""

    def test_single_entry(self) -> None:
        """One entry returns a one-element list."""
        # Arrange / Act / Assert
        assert parse_directive("FLT") == [Entry("FLT", None, MODE_LKG)]

    def test_multiple_entries_in_input_order(self) -> None:
        """Multiple comma-separated entries are returned in input order."""
        # Arrange / Act
        entries = parse_directive("FLT, Toric --merge-branch, carleson@v1")

        # Assert
        assert entries == [
            Entry("FLT", None, MODE_LKG),
            Entry("Toric", None, MODE_MERGE),
            Entry("carleson", "v1", MODE_LKG),
        ]

    def test_whitespace_around_entries_tolerated(self) -> None:
        """Extra whitespace around entries / separators is fine."""
        # Arrange / Act
        entries = parse_directive("  FLT  ,   Toric --merge-branch  ")

        # Assert
        assert entries == [
            Entry("FLT", None, MODE_LKG),
            Entry("Toric", None, MODE_MERGE),
        ]

    def test_empty_body_raises_with_usage_hint(self) -> None:
        """Empty body raises with the usage line as a hint.

        Catches a common mistake — typing the directive with no
        arguments at all.
        """
        # Arrange / Act
        with pytest.raises(GrammarError) as exc:
            parse_directive("")
        # Assert
        assert "no downstream entries" in exc.value.message
        assert "!downstream-check" in exc.value.hint

    def test_only_commas_and_whitespace_raises(self) -> None:
        """A body of only commas/whitespace is rejected the same way as empty."""
        # Arrange / Act / Assert
        with pytest.raises(GrammarError, match="no downstream entries"):
            parse_directive("  ,   ,  ")

    def test_dedup_collapses_exact_duplicates(self) -> None:
        """Entries that match on (name, rev, mode) collapse to one.

        Distinct fields are kept on purpose — `FLT, FLT@main` runs
        twice; `FLT, FLT --merge-branch` runs each in its own mode.
        Only entries that compare equal on the full triple collapse.
        """
        # Arrange / Act
        entries = parse_directive("FLT, FLT, FLT@main, FLT@main, FLT --merge-branch")

        # Assert
        assert entries == [
            Entry("FLT", None, MODE_LKG),
            Entry("FLT", "main", MODE_LKG),
            Entry("FLT", None, MODE_MERGE),
        ]

    def test_first_grammar_error_short_circuits(self) -> None:
        """The first bad entry stops parsing — we don't accumulate every error.

        Reporting one issue at a time keeps the PR comment focused;
        the user fixes that one and re-triggers.
        """
        # Arrange / Act
        with pytest.raises(GrammarError, match="--bogus"):
            parse_directive("FLT, Toric --bogus, carleson --also-bogus")


class TestSerialize:
    """`serialize` round-trips an Entry back to the directive form."""

    def test_bare_lkg(self) -> None:
        # Arrange / Act / Assert
        assert serialize(Entry("FLT", None, MODE_LKG)) == "FLT"

    def test_with_rev(self) -> None:
        # Arrange / Act / Assert
        assert serialize(Entry("FLT", "v1.2.3", MODE_LKG)) == "FLT@v1.2.3"

    def test_merge_mode_appends_flag(self) -> None:
        # Arrange / Act / Assert
        assert serialize(Entry("FLT", None, MODE_MERGE)) == "FLT --merge-branch"

    def test_rev_and_merge_flag_combined(self) -> None:
        # Arrange / Act / Assert
        assert (
            serialize(Entry("FLT", "v1", MODE_MERGE)) == "FLT@v1 --merge-branch"
        )

    def test_roundtrip_through_parse(self) -> None:
        """Every parse → serialize → parse roundtrip is the identity.

        The dispatched workflow re-parses `resolved_names` on its
        side; if `serialize` produces something its own parser
        can't read, the directive silently fails downstream.  Pin
        the contract.
        """
        # Arrange
        forms = [
            "FLT",
            "FLT@v1.2.3",
            "FLT --merge-branch",
            "FLT@v1.2.3 --merge-branch",
            "leanprover-community/FLT@main --merge-branch",
        ]
        # Act / Assert
        for form in forms:
            entry = parse_entry(form)
            assert parse_entry(serialize(entry)) == entry
