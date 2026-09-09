"""Render the Zulip message.

The headline, the severity bullets, and the error and panic spoiler tables are the
shell script's, byte for byte, as long as they fit. Warnings and info messages are never
listed one by one; they are summarised in a per-linter count table. Together with a cap
on the spoiler tables this keeps the message under Zulip's size limit, so a badly broken
build still posts a readable message. The full detail lives in the job summary (see
`summary`).
"""

from __future__ import annotations

from typing import Dict, List, Tuple

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


def _spoiler_table(title: str, column: str, rows: List[Tuple[int, str]], shown: int) -> str:
    out = [f"```spoiler {title}", f"| | {column} |", "| ---: | --- |"]
    out += [f"| {n} | {escape_cell(d)} |" for n, d in rows[:shown]]
    if shown < len(rows):
        out.append(f"| … | {len(rows) - shown} more not shown; see the job summary |")
    out += ["```", ""]
    return "\n".join(out) + "\n"


def render_zulip(messages: List[Message], ctx: ReportContext, limit: int = ZULIP_LIMIT) -> str:
    """The Zulip message: headline, severity counts, per-linter table, error/panic tables.

    If the message would exceed `limit` characters, the largest spoiler table is halved
    until it fits, with a trailing row saying how many rows were left out.
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

    rows = linter_table(messages, ctx.show_info)
    if any(w + i for _, w, i in rows):
        if ctx.show_info:
            out.append("| | Linter | Warnings | Info |\n| ---: | --- | ---: | ---: |\n")
            out += [f"| | {name} | {w} | {i} |\n" for name, w, i in rows]
        else:
            out.append("| | Linter | Warnings |\n| ---: | --- | ---: |\n")
            out += [f"| | {name} | {w} |\n" for name, w, _ in rows]
        out.append(f"\nFull per-linter tables, with source links, are in the [job summary]({ctx.run_url}).\n\n")

    tables: List[Tuple[str, str, List[Tuple[int, str]]]] = []  # (title, column, rows)
    panic_lines = [line for m in messages for line in m.panic_lines]
    if panic_lines:
        tables.append(("Panic counts", "Panic description", _description_counts(panic_lines)))
    errors = [m.first_line for m in messages if m.severity == "error"]
    if errors:
        tables.append(("Error counts", "Error description", _description_counts(errors)))

    shown = [len(rows) for _, _, rows in tables]

    def render() -> str:
        return "".join(out) + "".join(
            _spoiler_table(title, column, rows, n) for (title, column, rows), n in zip(tables, shown)
        )

    text = render()
    while len(text) > limit and any(n > 1 for n in shown):
        biggest = max(range(len(shown)), key=lambda k: shown[k])
        shown[biggest] //= 2
        text = render()
    return text
