"""Render the GitHub job summary.

One section per linter (plus errors and panics), one row per message, sorted by file and
line, each linking to the source at the built commit. GitHub caps a step's summary at
1 MiB, so the output is kept under `SUMMARY_LIMIT` by truncating the largest sections
first.
"""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

from .context import ReportContext
from .lake_log import UNATTRIBUTED, Message, linter_table, severity_counts
from .markdown import escape_cell

SUMMARY_LIMIT = 900_000  # GitHub caps job summaries at 1 MiB; leave headroom.


def source_link(msg: Message, ctx: ReportContext) -> str:
    """Markdown link to the message's source line at the built commit.

    Lake logs every path relative to the package that owns it, so a dependency's file
    (e.g. `Batteries/Data/List/Basic.lean`) looks just like one of ours. Only paths that
    exist under `ctx.source_root` are linked; the rest are shown as plain text.
    """
    if msg.file is None:
        return "(no position)"
    label = f"{msg.file}:{msg.line}"
    if not os.path.isfile(os.path.join(ctx.source_root, msg.file)):
        return label
    return f"[{label}]({ctx.target_url}/blob/{ctx.target_sha}/{msg.file}#L{msg.line})"


def _by_position(msgs: List[Message]) -> List[Message]:
    return sorted(msgs, key=lambda m: (m.file is None, m.file or "", m.line or 0, m.col or 0))


def _sorted_rows(msgs: List[Message], ctx: ReportContext) -> List[str]:
    return [f"| {source_link(m, ctx)} | {escape_cell(m.first_line)} |" for m in _by_position(msgs)]


def _panic_rows(msgs: List[Message], ctx: ReportContext) -> List[str]:
    """One row per `PANIC at` line; a stderr block can carry several."""
    return [
        f"| {source_link(m, ctx)} | {escape_cell(line)} |"
        for m in _by_position(msgs)
        for line in m.panic_lines
    ]


def _count_label(msgs: List[Message]) -> str:
    w = sum(1 for m in msgs if m.severity == "warning")
    i = sum(1 for m in msgs if m.severity == "info")
    parts = []
    if w:
        parts.append(f"{w} warnings")
    if i:
        parts.append(f"{i} info")
    return ", ".join(parts)


def render_summary(messages: List[Message], ctx: ReportContext, limit: int = SUMMARY_LIMIT) -> str:
    """The job summary: one section per linter (plus errors and panics), a row per message.

    If the result would exceed `limit` bytes, the largest sections are truncated first.
    """
    if not ctx.show_info:
        messages = [m for m in messages if m.severity != "info" or m.is_panic]
    head = [
        f"# {ctx.workflow_name}: {ctx.target_repo} @ {ctx.target_sha[:7]}\n",
        f"\nCommit [{ctx.target_sha}]({ctx.target_url}/commit/{ctx.target_sha}). Run [{ctx.run_id}]({ctx.run_url}).\n",
        "".join(f"\n* {label}: {n}" for label, n in severity_counts(messages).items()) + "\n",
    ]

    sections: List[Tuple[str, List[str]]] = []  # (heading, rows) in output order
    errors = [m for m in messages if m.severity == "error"]
    if errors:
        sections.append((f"## Errors ({len(errors)})", _sorted_rows(errors, ctx)))
    panics = [m for m in messages if m.is_panic]
    if panics:
        rows = _panic_rows(panics, ctx)
        sections.append((f"## Panics ({len(rows)})", rows))
    by_linter: Dict[str, List[Message]] = {}
    for m in messages:
        if m.severity == "error" or m.is_panic:
            continue
        by_linter.setdefault(m.linter or UNATTRIBUTED, []).append(m)
    for name, _, _ in linter_table(messages, ctx.show_info):
        msgs = by_linter.get(name)
        if msgs:
            sections.append((f"## {name} ({_count_label(msgs)})", _sorted_rows(msgs, ctx)))

    shown = [len(rows) for _, rows in sections]

    def render() -> str:
        out = list(head)
        for (heading, rows), n in zip(sections, shown):
            out.append(f"\n{heading}\n\n| Location | Message |\n| --- | --- |\n")
            out += [r + "\n" for r in rows[:n]]
            if n < len(rows):
                out.append(f"| … | {len(rows) - n} more rows not shown |\n")
        return "".join(out)

    text = render()
    while len(text.encode("utf-8")) > limit and any(shown):
        biggest = max(range(len(shown)), key=lambda k: shown[k])
        shown[biggest] //= 2
        text = render()
    return text
