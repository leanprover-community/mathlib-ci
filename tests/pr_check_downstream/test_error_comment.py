"""Tests for error_comment.render (the HTTP POST is not covered)."""

from __future__ import annotations

from error_comment import render
from grammar import GrammarError


class TestRender:
    def test_mention_renders_first_when_commenter_known(self) -> None:
        """A known login leads with an @-mention so the commenter gets notified."""
        body = render(commenter="marcelolynch", error=GrammarError("unknown flag `--foo`"))
        assert body.splitlines()[0].startswith("Hi @marcelolynch,")

    def test_fallback_greeting_when_no_commenter(self) -> None:
        """No commenter (e.g. a workflow_dispatch invocation) falls back to a plain greeting."""
        body = render(commenter="", error=GrammarError("anything"))
        assert body.splitlines()[0].startswith("Hi,")
        assert "@," not in body  # no stray `@` from an empty mention

    def test_error_message_appears_in_quote_block(self) -> None:
        """The parser's error text lands verbatim in the Markdown quote block."""
        err = GrammarError("unknown flag `--bogus` in entry `FLT --bogus`")
        body = render(commenter="marcelolynch", error=err)
        assert "> unknown flag `--bogus` in entry `FLT --bogus`" in body

    def test_hint_appears_as_second_quote_line_when_set(self) -> None:
        """The hint is how the user recovers, so it renders as a second quote line."""
        err = GrammarError("unknown flag `--bogus`", hint="only `--merge-branch` is supported")
        body = render(commenter="m", error=err)
        assert "> (only `--merge-branch` is supported)" in body

    def test_no_hint_line_when_hint_empty(self) -> None:
        """An empty hint omits the second quote line entirely."""
        body = render(commenter="m", error=GrammarError("any message"))
        assert len([l for l in body.splitlines() if l.startswith("> ")]) == 1

    def test_carries_usage_reminder_and_docs_link(self) -> None:
        """Every error stays self-contained: inline grammar reminder + design-doc link."""
        body = render(commenter="m", error=GrammarError("anything"))
        assert "!downstream-check" in body
        assert "<name-or-slug>" in body
        assert "--merge-branch" in body
        assert "pr-validation-workflow.md" in body
