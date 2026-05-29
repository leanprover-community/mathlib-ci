"""Tests for ack.render / ack._render_entries (the HTTP POST is not covered)."""

from __future__ import annotations

from ack import _render_entries, render

_MERGE_SHA = "299461184e256b5f1c8b940b830ca1fce7377aee"
_RUN_URL = "https://github.com/example/repo/actions/runs/12345"


class TestRender:
    def test_heading_is_bold(self) -> None:
        """Bold (not `#`) matches the dispatch result comment's quieter rendering."""
        body = render(downstreams="FLT", merge_sha=_MERGE_SHA, run_url=_RUN_URL)
        assert body.splitlines()[0] == "**Downstream validation triggered**"

    def test_short_sha_used_in_body(self) -> None:
        """The full SHA is truncated to 7 chars in the rendered body."""
        body = render(downstreams="FLT", merge_sha=_MERGE_SHA, run_url=_RUN_URL)
        assert f"`{_MERGE_SHA[:7]}`" in body
        assert _MERGE_SHA not in body, "only the short SHA should appear"

    def test_entries_listed_with_backtick_quoting(self) -> None:
        """Each entry is wrapped in backticks so it reads as a token."""
        body = render(
            downstreams="FLT, Toric --merge-branch, carleson@v1",
            merge_sha=_MERGE_SHA,
            run_url=_RUN_URL,
        )
        assert "`FLT`" in body
        assert "`Toric --merge-branch`" in body
        assert "`carleson@v1`" in body

    def test_mode_paragraph_present(self) -> None:
        """A first-time reader won't know what `--merge-branch` does — spell it out."""
        body = render(downstreams="FLT", merge_sha=_MERGE_SHA, run_url=_RUN_URL)
        assert "LKG mode by default" in body
        assert "--merge-branch" in body

    def test_run_link_present(self) -> None:
        """The dispatch run link lets the requester follow progress."""
        body = render(downstreams="FLT", merge_sha=_MERGE_SHA, run_url=_RUN_URL)
        assert f"[run]({_RUN_URL})" in body


class TestRenderEntries:
    def test_simple_list(self) -> None:
        """A clean comma-separated list renders as `, `-joined backticked tokens."""
        assert _render_entries("FLT, Toric, carleson") == "`FLT`, `Toric`, `carleson`"

    def test_empty_segments_dropped(self) -> None:
        """Consecutive or trailing commas produce no empty tokens."""
        assert _render_entries("FLT,,Toric,") == "`FLT`, `Toric`"

    def test_backticks_in_rev_stripped(self) -> None:
        """A backtick mid-token would close the code span — `git check-ref-format`
        permits backticks in refnames, so they must be stripped, not rendered."""
        out = _render_entries("FLT@`pwned` --merge-branch")
        assert out == "`FLT@pwned --merge-branch`"
        assert out.count("`") == 2
