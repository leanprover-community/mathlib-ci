"""Tests for updateDeclsDiffSection's marker-based splicing."""

from __future__ import annotations

from updateDeclsDiffSection import (
    BEGIN,
    END,
    PR_SUMMARY_PREFIX,
    WARNING_MARKER,
    build_warning,
    splice_success,
    splice_warning,
)

SECTION = "#### Declarations diff\n\n> ✅ **Lean-aware diff**\n\n* **+1** new declarations\n"


def comment(inner: str) -> str:
    """A minimal `### PR summary` comment with `inner` inside the markers."""
    return (
        f"{PR_SUMMARY_PREFIX} [abc1234](url)\n\n"
        "#### Import changes for modified files\n\nfoo\n\n---\n\n"
        f"{BEGIN}\n{inner}\n{END}\n\n---\n\n"
        "No changes to technical debt.\n"
    )


class TestSpliceSuccess:
    def test_replaces_region_content(self) -> None:
        """The old marked content is gone and the new section is spliced in."""
        body = splice_success(comment("#### Declarations diff\n\nOLD regex diff\n"), SECTION)
        assert "OLD regex diff" not in body
        assert "**+1** new declarations" in body

    def test_keeps_surrounding_parts_and_markers(self) -> None:
        """Splicing touches only the region; other parts and markers survive."""
        body = splice_success(comment("OLD\n"), SECTION)
        assert "#### Import changes for modified files" in body
        assert "No changes to technical debt." in body
        assert body.count(BEGIN) == 1 and body.count(END) == 1

    def test_idempotent(self) -> None:
        """Re-splicing the same section is a no-op on the already-patched body."""
        once = splice_success(comment("OLD\n"), SECTION)
        twice = splice_success(once, SECTION)
        assert twice == once

    def test_noop_without_markers(self) -> None:
        """A comment lacking the markers is returned byte-for-byte unchanged."""
        body = f"{PR_SUMMARY_PREFIX}\n\n#### Declarations diff\n\nlegacy\n"
        assert splice_success(body, SECTION) == body

    def test_summary_prefix_preserved(self) -> None:
        """Guardrail: the leading `### PR summary` line (used to locate the
        comment) is never rewritten by a patch."""
        body = splice_success(comment("OLD\n"), SECTION)
        assert body.startswith(PR_SUMMARY_PREFIX)


class TestSpliceWarning:
    def test_appends_marker_and_keeps_existing_diff(self) -> None:
        """Warning mode keeps the existing (regex) diff and adds the notice."""
        body = splice_warning(comment("#### Declarations diff\n\nregex diff\n"),
                              build_warning("master"))
        assert "regex diff" in body
        assert WARNING_MARKER in body
        assert "Merge `master`" in body

    def test_idempotent(self) -> None:
        """A region already carrying the warning marker is left unchanged."""
        once = splice_warning(comment("regex diff\n"), build_warning("master"))
        twice = splice_warning(once, build_warning("master"))
        assert twice == once

    def test_noop_without_markers(self) -> None:
        body = f"{PR_SUMMARY_PREFIX}\n\nno markers here\n"
        assert splice_warning(body, build_warning("master")) == body

    def test_summary_prefix_preserved(self) -> None:
        body = splice_warning(comment("regex diff\n"), build_warning("master"))
        assert body.startswith(PR_SUMMARY_PREFIX)
