"""Parser for the `!downstream-check` directive grammar.

Each comma-separated entry of the directive parses to an :class:`Entry`:

    <name-or-slug>[@<rev>] [--merge-branch]

* ``<name-or-slug>`` — required.  Any non-empty token; the dispatched
  downstream-reports workflow resolves it against its inventory (short
  name or ``owner/repo`` slug).
* ``@<rev>`` — optional.  Any git refspec for the downstream's checkout.
* ``--merge-branch`` — optional flag flipping that single entry from
  LKG mode (the default) to merge mode.

Grammar errors raised by :func:`parse_directive` are surfaced to the PR
author as a comment-on-failure via :mod:`error_comment`.  Runtime
errors (unknown downstream, build failures, etc.) come from the
dispatched workflow's own reporting — this side is grammar-only.
"""

from __future__ import annotations

import dataclasses

MODE_LKG = "lkg"
MODE_MERGE = "merge"
MERGE_FLAG = "--merge-branch"


@dataclasses.dataclass(frozen=True)
class Entry:
    """One parsed `<name-or-slug>[@<rev>] [--merge-branch]` entry."""

    name_or_slug: str
    rev: str | None
    mode: str  # MODE_LKG | MODE_MERGE


class GrammarError(Exception):
    """Raised on directive-grammar violations.

    ``message`` is the one-line user-facing description shown in the
    error comment.  ``hint`` is an optional follow-up sentence shown
    on a second line of the quote block.
    """

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def parse_directive(entries: str) -> list[Entry]:
    """Parse the comma-separated tail of a ``!downstream-check`` directive.

    *entries* is the directive body with the ``!downstream-check``
    prefix already stripped (the calling workflow does the strip
    because the ``startsWith`` check on the comment body lives in YAML
    anyway).  Returns the deduplicated entries in input order; raises
    :class:`GrammarError` on any syntax violation.
    """
    body = entries.strip()
    if not body:
        raise GrammarError(
            "no downstream entries provided",
            hint=(
                "usage: `!downstream-check <name-or-slug>[@<rev>]"
                " [--merge-branch][, ...]`"
            ),
        )
    parsed: list[Entry] = []
    for raw in (segment.strip() for segment in body.split(",")):
        if not raw:
            continue
        parsed.append(parse_entry(raw))
    if not parsed:
        raise GrammarError(
            "no downstream entries provided",
            hint=(
                "usage: `!downstream-check <name-or-slug>[@<rev>]"
                " [--merge-branch][, ...]`"
            ),
        )
    return _dedup(parsed)


def parse_entry(raw: str) -> Entry:
    """Parse one whitespace-delimited entry into an :class:`Entry`.

    Tokens are: first the ``<name-or-slug>[@<rev>]`` chunk, then zero
    or more flag tokens.  Currently only ``--merge-branch`` is a known
    flag; anything else raises with a hint naming the valid set.
    """
    tokens = raw.split()
    if not tokens:
        raise GrammarError(f"empty entry: `{raw}`")
    name_rev = tokens[0]
    flags = tokens[1:]

    if "@" in name_rev:
        bare, _, rev = name_rev.partition("@")
        rev_value: str | None = rev.strip() or None
        if rev_value is None:
            raise GrammarError(f"empty rev after `@` in entry: `{raw}`")
    else:
        bare = name_rev
        rev_value = None

    bare = bare.strip()
    if not bare:
        raise GrammarError(f"empty downstream name in entry: `{raw}`")

    mode = MODE_LKG
    for flag in flags:
        if flag == MERGE_FLAG:
            mode = MODE_MERGE
        else:
            raise GrammarError(
                f"unknown flag `{flag}` in entry `{raw}`",
                hint=f"only `{MERGE_FLAG}` is supported",
            )

    return Entry(name_or_slug=bare, rev=rev_value, mode=mode)


def serialize(entry: Entry) -> str:
    """Render an :class:`Entry` back to its directive form.

    Used for the ``resolved_names`` output: the dispatched workflow
    receives the same grammar back, so its own parser sees a known-good
    string.
    """
    s = entry.name_or_slug
    if entry.rev:
        s = f"{s}@{entry.rev}"
    if entry.mode == MODE_MERGE:
        s = f"{s} {MERGE_FLAG}"
    return s


def _dedup(entries: list[Entry]) -> list[Entry]:
    """Drop exact-duplicate entries while preserving first-occurrence order.

    Distinct fields are intentionally kept (``FLT, FLT@main`` runs
    twice; ``FLT, FLT --merge-branch`` runs each entry in its own
    mode); only entries that compare equal on the full triple
    collapse.
    """
    seen: set[tuple[str, str | None, str]] = set()
    out: list[Entry] = []
    for e in entries:
        key = (e.name_or_slug, e.rev, e.mode)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out
