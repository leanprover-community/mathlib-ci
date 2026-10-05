"""Render the Zulip message.

The headline, the severity bullets, and the error and panic spoiler tables are the
shell script's, byte for byte, as long as they fit. Warnings and info messages are never
listed one by one; they are summarised in a per-linter count table. Together with a cap
on the spoiler tables this keeps the message under Zulip's size limit, so a badly broken
build still posts a readable message. The full detail lives in the job summary (see
`summary`).
"""

from __future__ import annotations

from textwrap import dedent
from typing import Callable, Dict, List, Tuple

from .context import ReportContext
from .lake_log import Message, linter_table, severity_counts
from .markdown import escape_cell

ZULIP_LIMIT = 10_000  # Zulip's default `max_message_length`, in characters.


def _description_counts(descriptions: List[str]) -> List[Tuple[int, str]]:
    """(count, description) pairs ordered like the shell's `sort | uniq -c | sort -bgr`.

    That is: count descending, ties in reverse byte order of the text (`-r` also
    reverses sort's last-resort comparison).
    """
    counts: Dict[str, int] = {}
    for d in descriptions:
        counts[d] = counts.get(d, 0) + 1
    rows = sorted(counts.items(), key=lambda r: r[0].encode("utf-8"), reverse=True)
    return sorted(((n, d) for d, n in rows), key=lambda r: -r[0])


def _spoiler_table(title: str, column: str, rows: List[Tuple[int, str]], shown: int, ctx: ReportContext) -> str:
    out = [dedent(f"""\
        ```spoiler {title}
        | | {column} |
        | ---: | --- |
        """)]
    out += [f"| {n} | {escape_cell(d)} |\n" for n, d in rows[:shown]]
    if shown < len(rows):
        where = "; see the job summary" if ctx.summary_path else ""
        out.append(f"| … | {len(rows) - shown} more not shown{where} |\n")
    out.append("```\n\n")
    return "".join(out)


def _linter_table(rows: List[Tuple[str, int, int]], shown: int, ctx: ReportContext) -> str:
    if ctx.show_info:
        out = ["| | Linter | Warnings | Info |\n| ---: | --- | ---: | ---: |\n"]
        out += [f"| | {name} | {w} | {i} |\n" for name, w, i in rows[:shown]]
    else:
        out = ["| | Linter | Warnings |\n| ---: | --- | ---: |\n"]
        out += [f"| | {name} | {w} |\n" for name, w, _ in rows[:shown]]
    if shown < len(rows):
        out.append(f"| | … {len(rows) - shown} more not shown | |{' |' if ctx.show_info else ''}\n")
    if ctx.summary_path:
        out.append(f"\nFull per-linter tables, with source links, are in the [job summary]({ctx.run_url}).\n")
    out.append("\n")
    return "".join(out)


def render_zulip(messages: List[Message], ctx: ReportContext, limit: int = ZULIP_LIMIT) -> str:
    """The Zulip message: headline, severity counts, per-linter table, error/panic tables.

    If the message would exceed `limit` characters, the table taking the most room (the
    per-linter table or a spoiler table) is halved until it fits, down to a single
    pointer row saying how many rows were left out.
    """
    icon, ended = ("✅", "succeeded") if ctx.success else ("❌", "failed")
    head = (
        f"{icon} {ctx.workflow_name} run on [{ctx.target_repo}]({ctx.target_url}) "
        f"(commit [{ctx.target_sha}]({ctx.target_url}/commit/{ctx.target_sha}))"
    )
    counts = severity_counts(messages)
    if not counts:
        return f"{head} [{ended} without messages]({ctx.run_url}).\n"

    out = [f"{head} [{ended} with messages]({ctx.run_url}):\n",
           "".join(f"\n* {label}: {n}" for label, n in counts.items()) + "\n\n"]

    # Each table is rendered with its first `shown[k]` rows; the rest is a pointer row.
    tables: List[Callable[[int], str]] = []
    shown: List[int] = []

    linters = linter_table(messages, ctx.show_info)
    if any(w + i for _, w, i in linters):
        tables.append(lambda n: _linter_table(linters, n, ctx))
        shown.append(len(linters))

    panic_lines = [line for m in messages for line in m.panic_lines]
    if panic_lines:
        panics = _description_counts(panic_lines)
        tables.append(lambda n: _spoiler_table("Panic counts", "Panic description", panics, n, ctx))
        shown.append(len(panics))
    error_lines = [m.first_line for m in messages if m.severity == "error"]
    if error_lines:
        errors = _description_counts(error_lines)
        tables.append(lambda n: _spoiler_table("Error counts", "Error description", errors, n, ctx))
        shown.append(len(errors))

    def render() -> str:
        return "".join(out) + "".join(table(n) for table, n in zip(tables, shown))

    text = render()
    while len(text) > limit and any(shown):
        # Halve the table that takes the most room (one long error can outweigh many rows).
        biggest = max((k for k in range(len(shown)) if shown[k]), key=lambda k: len(tables[k](shown[k])))
        shown[biggest] //= 2
        text = render()
    return text
