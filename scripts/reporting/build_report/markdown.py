"""Markdown helpers shared by the two renderers."""

from __future__ import annotations


def escape_cell(text: str) -> str:
    """Make `text` safe inside a Markdown table cell (a bare `|` would split the row)."""
    return text.replace("|", "\\|")
