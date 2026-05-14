"""
Tests for: error_comment.render

Coverage scope:
    - The error comment opens with a notification-triggering @-mention
      when the commenter login is known.
    - Falls back to "Hi," when no commenter is given (e.g. a
      workflow_dispatch invocation).
    - The parser's error text lands in the quoted block so the user
      sees exactly what failed.
    - The optional `hint` shows up as a second quoted line.
    - The body carries a usage reminder and a link to the design doc.

Out of scope:
    - The HTTP POST (`error_comment.post`) — exercised manually
      during smoke runs.
"""

from __future__ import annotations

from error_comment import render
from grammar import GrammarError


class TestRender:
    """`render` produces the Markdown body of the error comment."""

    def test_mention_renders_first_when_commenter_known(self) -> None:
        """A known commenter login leads to an `@mention` so they get notified."""
        # Arrange
        err = GrammarError("unknown flag `--foo`")

        # Act
        body = render(commenter="marcelolynch", error=err)

        # Assert
        assert body.splitlines()[0].startswith("Hi @marcelolynch,")

    def test_fallback_greeting_when_no_commenter(self) -> None:
        """Empty commenter falls back to a plain "Hi,"."""
        # Arrange
        err = GrammarError("anything")

        # Act
        body = render(commenter="", error=err)

        # Assert
        assert body.splitlines()[0].startswith("Hi,")
        # No stray `@` from an empty mention.
        assert "@," not in body

    def test_error_message_appears_in_quote_block(self) -> None:
        """The parser's `error.message` shows up as a Markdown quote line."""
        # Arrange
        err = GrammarError("unknown flag `--bogus` in entry `FLT --bogus`")

        # Act
        body = render(commenter="marcelolynch", error=err)

        # Assert
        assert "> unknown flag `--bogus` in entry `FLT --bogus`" in body

    def test_hint_appears_as_second_quote_line_when_set(self) -> None:
        """An optional hint shows as a parenthesised second quote line.

        The hint is what tells the user how to recover from the
        error — e.g. naming the only allowed flag.
        """
        # Arrange
        err = GrammarError(
            "unknown flag `--bogus`",
            hint="only `--merge-branch` is supported",
        )

        # Act
        body = render(commenter="m", error=err)

        # Assert
        assert "> (only `--merge-branch` is supported)" in body

    def test_no_hint_line_when_hint_empty(self) -> None:
        """Empty hint omits the second quote line entirely."""
        # Arrange
        err = GrammarError("any message")

        # Act
        body = render(commenter="m", error=err)

        # Assert
        quote_lines = [l for l in body.splitlines() if l.startswith("> ")]
        assert len(quote_lines) == 1

    def test_usage_reminder_is_present(self) -> None:
        """Every error comment carries the directive grammar as a reminder.

        Showing the grammar inline saves a click and keeps the
        comment self-contained for someone who's correcting their
        directive from a notification email.
        """
        # Arrange
        err = GrammarError("anything")

        # Act
        body = render(commenter="m", error=err)

        # Assert
        assert "!downstream-check" in body
        assert "<name-or-slug>" in body
        assert "--merge-branch" in body

    def test_docs_link_present(self) -> None:
        """The body links to the design doc for the full reference."""
        # Arrange
        err = GrammarError("anything")

        # Act
        body = render(commenter="m", error=err)

        # Assert
        assert "downstream-reports" in body
        assert "pr-validation-workflow.md" in body
