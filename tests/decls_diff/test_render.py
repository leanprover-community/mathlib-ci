"""Tests for declsDiff.render_override / sanitize."""

from __future__ import annotations

import pytest

from declsDiff import (
    DETAILS_LINE_THRESHOLD,
    MAX_RENDERED_LINES,
    render_override,
    sanitize,
)


class TestRenderOverride:
    def test_no_changes_says_so(self) -> None:
        """With zero changes the body contains the `No declaration differences` literal."""
        body = render_override(0, 0, [], head_sha=None)
        assert "_No declaration differences._" in body
        assert "```diff" not in body

    def test_counts_appear_with_bold_signs(self) -> None:
        """Plus / minus counts render as `**+N**` and `**−M**` (note the typographic minus)."""
        body = render_override(3, 1, [], head_sha=None)
        assert "**+3** new declarations" in body
        assert "**−1** removed declarations" in body

    def test_short_sha_in_stamp(self) -> None:
        """`head_sha` truncates to its 7-char prefix in the stamp."""
        body = render_override(1, 0, [("+", "A")], head_sha="abcdef1234567890")
        assert "(commit `abcdef1`)" in body

    def test_missing_sha_drops_the_commit_clause(self) -> None:
        """A `None` SHA omits the `(commit ...)` parenthetical."""
        body = render_override(1, 0, [("+", "A")], head_sha=None)
        assert "Lean-aware diff" in body
        assert "commit" not in body

    def test_diff_block_contains_all_rows_below_cap(self) -> None:
        """If the diff fits under the cap, every row appears in the fenced block."""
        diff = [("+", f"A{i:03d}") for i in range(5)]
        body = render_override(5, 0, diff, head_sha=None)
        for _, name in diff:
            assert f"+{name}" in body
        assert "_(showing first" not in body

    def test_diff_truncates_above_cap(self) -> None:
        """Diffs longer than `MAX_RENDERED_LINES` show a truncation notice + the first N rows."""
        diff = [("+", f"A{i:04d}") for i in range(MAX_RENDERED_LINES + 5)]
        body = render_override(len(diff), 0, diff, head_sha=None)
        assert f"_(showing first {MAX_RENDERED_LINES} of {MAX_RENDERED_LINES + 5} lines)_" in body
        # First row is in the body; row past the cap is not.
        assert "+A0000" in body
        assert f"+A{MAX_RENDERED_LINES + 4:04d}" not in body

    def test_body_ends_with_newline(self) -> None:
        """The rendered body always ends with a trailing newline for clean concatenation."""
        assert render_override(0, 0, [], head_sha=None).endswith("\n")
        assert render_override(1, 0, [("+", "A")], head_sha=None).endswith("\n")


class TestSanitize:
    def test_plain_name_unchanged(self) -> None:
        """ASCII names with no line-breaking characters pass through untouched."""
        assert sanitize("Mathlib.Topology.Defs") == "Mathlib.Topology.Defs"

    @pytest.mark.parametrize("ch,esc", [
        ("\n", r"\n"),
        ("\r", r"\r"),
        (" ", r" "),
        (" ", r" "),
    ])
    def test_line_breaks_become_backslash_escapes(self, ch: str, esc: str) -> None:
        """Every line-break character becomes a visible backslash escape."""
        assert sanitize(f"foo{ch}bar") == f"foo{esc}bar"

    def test_render_with_injected_newline_stays_single_line(self) -> None:
        """A name containing `\\n` does not break the rendered diff into two lines."""
        injected = "foo\n## Phishing"
        body = render_override(1, 0, [("+", injected)], head_sha=None)
        # The "## Phishing" must not appear at the start of a line — sanitisation
        # must have collapsed the newline into a `\n` escape.
        for line in body.splitlines():
            assert not line.startswith("## Phishing")
        assert r"+foo\n## Phishing" in body

    def test_comment_closer_escaped(self) -> None:
        """`-->` is escaped so a name can't forge the closing region marker."""
        assert sanitize("foo-->bar") == r"foo--\>bar"

    @pytest.mark.parametrize("marker", [
        "<!-- DECLS_DIFF_LEAN_END -->",
        "<!-- DECLS_DIFF_LEAN_BEGIN -->",
    ])
    def test_no_marker_survives_sanitize(self, marker: str) -> None:
        """No region marker can survive sanitisation intact (all end in `-->`)."""
        assert marker not in sanitize(f"x{marker}y")

    def test_comment_closer_escape_is_idempotent(self) -> None:
        """Re-sanitising an already-escaped name reintroduces no `-->`."""
        once = sanitize("a-->b")
        assert sanitize(once) == once


class TestWithHeading:
    def test_default_omits_heading_and_wrap(self) -> None:
        """Without the flag the output is just the body — no heading, no <details>."""
        body = render_override(1, 0, [("+", "A")], head_sha=None)
        assert "#### Declarations diff" not in body
        assert "<details>" not in body

    def test_flag_adds_heading(self) -> None:
        """`with_heading=True` prepends the section heading."""
        body = render_override(0, 0, [], head_sha=None, with_heading=True)
        assert body.lstrip().startswith("#### Declarations diff")
        assert "<details>" not in body  # short body stays inline

    def test_long_body_is_details_wrapped(self) -> None:
        """A body exceeding the newline threshold is wrapped in <details>."""
        diff = [("+", f"A{i:03d}") for i in range(DETAILS_LINE_THRESHOLD + 5)]
        body = render_override(len(diff), 0, diff, head_sha=None, with_heading=True)
        assert "<details><summary>" in body
        assert "#### Declarations diff" in body
        assert body.rstrip().endswith("</details>")
