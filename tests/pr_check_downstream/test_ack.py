"""
Tests for: ack.render / ack._render_entries

Coverage scope:
    - The ack body's structure: heading line, validating line with
      short SHA + entry list, mode-description paragraph, dispatch
      run link.
    - Each entry is backtick-quoted in the rendered list.
    - Backticks inside an entry's rev get stripped before wrapping —
      a `git check-ref-format`-permitted backtick must not break out of
      the rendered code span.
    - Whitespace / empty segments around commas are tolerated.

Out of scope:
    - The HTTP POST (`ack.post`).
"""

from __future__ import annotations

from ack import _render_entries, render


_MERGE_SHA = "299461184e256b5f1c8b940b830ca1fce7377aee"
_RUN_URL = "https://github.com/example/repo/actions/runs/12345"


class TestRender:
    """`render` builds the dispatch-time ack comment."""

    def test_heading_is_bold(self) -> None:
        """The ack opens with a bold heading line.

        Bold (not `#`/`##`) matches the dispatch result comment's
        quieter rendering on the downstream-reports side, keeping the
        PR conversation visually unified.
        """
        # Arrange / Act
        body = render(
            downstreams="FLT, Toric --merge-branch",
            merge_sha=_MERGE_SHA,
            run_url=_RUN_URL,
        )

        # Assert
        assert body.splitlines()[0] == "**Downstream validation triggered**"

    def test_short_sha_used_in_body(self) -> None:
        """Full SHA is truncated to 7 chars in the rendered body."""
        # Arrange / Act
        body = render(
            downstreams="FLT",
            merge_sha=_MERGE_SHA,
            run_url=_RUN_URL,
        )

        # Assert
        assert f"`{_MERGE_SHA[:7]}`" in body
        assert _MERGE_SHA not in body, "full SHA should not appear; only short form"

    def test_entries_listed_with_backtick_quoting(self) -> None:
        """Each entry is wrapped in backticks so it reads as a token."""
        # Arrange / Act
        body = render(
            downstreams="FLT, Toric --merge-branch, carleson@v1",
            merge_sha=_MERGE_SHA,
            run_url=_RUN_URL,
        )

        # Assert
        assert "`FLT`" in body
        assert "`Toric --merge-branch`" in body
        assert "`carleson@v1`" in body

    def test_mode_paragraph_present(self) -> None:
        """The mode-description paragraph explains LKG vs merge for the reader.

        The directive grammar makes `--merge-branch` opt-in, but a
        first-time reader on the PR won't know what that flag does;
        the paragraph documents the default + flip without making
        them click through.
        """
        # Arrange / Act
        body = render(
            downstreams="FLT",
            merge_sha=_MERGE_SHA,
            run_url=_RUN_URL,
        )

        # Assert
        assert "LKG mode by default" in body
        assert "--merge-branch" in body

    def test_run_link_present(self) -> None:
        """The dispatch run link lets the requester follow progress."""
        # Arrange / Act
        body = render(
            downstreams="FLT",
            merge_sha=_MERGE_SHA,
            run_url=_RUN_URL,
        )

        # Assert
        assert f"[run]({_RUN_URL})" in body


class TestRenderEntries:
    """`_render_entries` strips dangerous characters and renders backticked tokens."""

    def test_simple_list(self) -> None:
        """A clean comma-separated list renders as `, `-joined backticked tokens."""
        # Arrange / Act
        out = _render_entries("FLT, Toric, carleson")

        # Assert
        assert out == "`FLT`, `Toric`, `carleson`"

    def test_whitespace_around_separators_tolerated(self) -> None:
        """Extra whitespace around entries / commas is trimmed."""
        # Arrange / Act
        out = _render_entries("  FLT  ,   Toric   ")

        # Assert
        assert out == "`FLT`, `Toric`"

    def test_empty_segments_dropped(self) -> None:
        """Empty entries (consecutive commas or trailing comma) are skipped."""
        # Arrange / Act
        out = _render_entries("FLT,,Toric,")

        # Assert
        assert out == "`FLT`, `Toric`"

    def test_backticks_in_rev_stripped(self) -> None:
        """A backtick mid-token would close the code span — strip it.

        `git check-ref-format` allows backticks in refnames (legal,
        if unusual), so a malicious or accidental backtick must not
        break out of the rendered backtick-quoted span.
        """
        # Arrange
        # The "rev" here contains a stray backtick; rendering must drop
        # it so the surrounding code span stays intact.
        downstreams = "FLT@`pwned` --merge-branch"

        # Act
        out = _render_entries(downstreams)

        # Assert
        assert out == "`FLT@pwned --merge-branch`"
        # Defence in depth: the rendered text contains exactly two
        # backticks (one pair around the token), nothing more.
        assert out.count("`") == 2
