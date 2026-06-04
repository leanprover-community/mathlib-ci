"""Tests for updateDeclsDiffSection's marker-based splicing."""

from __future__ import annotations

from updateDeclsDiffSection import (
    LEAN_BEGIN,
    LEAN_END,
    PR_SUMMARY_PREFIX,
    build_warning,
    find_summary_comment,
    splice,
)

# A success block as produced by `declsDiff.py --with-heading`.
SECTION = "#### Declarations diff (Lean)\n\n> ✅ **Lean-aware diff**\n\n* **+1** new declarations\n"


def comment(inner: str) -> str:
    """A minimal `### PR summary` comment carrying the regex block plus a Lean
    region whose contents are `inner`."""
    return (
        f"{PR_SUMMARY_PREFIX} [abc1234](url)\n\n"
        "#### Import changes for modified files\n\nfoo\n\n---\n\n"
        "#### Declarations diff (regex)\n\n`+ Foo`\n\n---\n\n"
        f"{LEAN_BEGIN}\n{inner}\n{LEAN_END}\n\n---\n\n"
        "No changes to technical debt.\n"
    )


PLACEHOLDER = "#### Declarations diff (Lean -- pending)\n\n_Computed after the build finishes._"


class TestSplice:
    def test_replaces_region_content(self) -> None:
        """The placeholder is gone and the new section is spliced in."""
        body = splice(comment(PLACEHOLDER), SECTION)
        assert "pending" not in body
        assert "**+1** new declarations" in body
        assert "#### Declarations diff (Lean)" in body

    def test_keeps_regex_block_and_markers(self) -> None:
        """Splicing touches only the Lean region; the regex block and markers survive."""
        body = splice(comment(PLACEHOLDER), SECTION)
        assert "#### Declarations diff (regex)" in body
        assert "`+ Foo`" in body
        assert "#### Import changes for modified files" in body
        assert body.count(LEAN_BEGIN) == 1 and body.count(LEAN_END) == 1

    def test_idempotent(self) -> None:
        """Re-splicing the same block is a no-op on the already-patched body."""
        once = splice(comment(PLACEHOLDER), SECTION)
        assert splice(once, SECTION) == once

    def test_noop_without_markers(self) -> None:
        """A comment lacking the Lean markers is returned byte-for-byte unchanged."""
        body = f"{PR_SUMMARY_PREFIX}\n\n#### Declarations diff (regex)\n\nlegacy\n"
        assert splice(body, SECTION) == body

    def test_summary_prefix_preserved(self) -> None:
        """Guardrail: the leading `### PR summary` line (used to locate the
        comment) is never rewritten by a patch."""
        assert splice(comment(PLACEHOLDER), SECTION).startswith(PR_SUMMARY_PREFIX)


class TestWarning:
    def test_unavailable_heading_and_keeps_regex(self) -> None:
        """Warning mode shows the `(Lean -- unavailable)` status and leaves the
        regex block intact."""
        body = splice(comment(PLACEHOLDER), build_warning("master"))
        assert "#### Declarations diff (Lean -- unavailable)" in body
        assert "Merge `master`" in body
        assert "#### Declarations diff (regex)" in body
        assert "`+ Foo`" in body

    def test_idempotent(self) -> None:
        once = splice(comment(PLACEHOLDER), build_warning("master"))
        assert splice(once, build_warning("master")) == once

    def test_summary_prefix_preserved(self) -> None:
        assert splice(comment(PLACEHOLDER), build_warning("master")).startswith(PR_SUMMARY_PREFIX)


class TestFindSummaryComment:
    def test_finds_the_summary_among_others(self) -> None:
        """The `### PR summary` comment is picked out of a list of comments."""
        comments = [
            {"body": "a normal review comment"},
            {"body": f"{PR_SUMMARY_PREFIX} [abc](url)\n\nbody"},
            {"body": "another comment"},
        ]
        assert find_summary_comment(comments)["body"].startswith(PR_SUMMARY_PREFIX)

    def test_returns_none_when_absent(self) -> None:
        assert find_summary_comment([{"body": "nope"}]) is None
        assert find_summary_comment([]) is None

    def test_tolerates_null_body(self) -> None:
        """A comment with a `null` body (allowed by the API) is skipped, not fatal."""
        comments = [
            {"body": None},
            {"body": f"{PR_SUMMARY_PREFIX}\n\nbody"},
        ]
        assert find_summary_comment(comments)["body"].startswith(PR_SUMMARY_PREFIX)
