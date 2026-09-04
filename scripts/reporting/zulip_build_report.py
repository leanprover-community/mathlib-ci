#!/usr/bin/env python3
"""Summarise a `lake build` log for Zulip and the GitHub job summary.

Reads the text log of a `lake build` (typically the weekly linting run), groups the
messages by the linter that produced them, and emits:

* on stdout, a `zulip-message<<DELIM ... DELIM` block for `GITHUB_OUTPUT`, holding a
  compact report: severity counts, a per-linter count table, and spoiler tables for
  errors and panics;
* appended to `GITHUB_STEP_SUMMARY` (when set), one section per linter with a row per
  occurrence, linking to the source at the reported commit.

Messages are attributed to a linter through the note that `Lean.Linter.logLint` appends,
"This linter can be disabled with `set_option linter.X false`". Messages without that note
are reported under "(not attributed to a linter)".

Environment (first non-empty wins):
  TARGET_REPO | REPO | GITHUB_REPOSITORY      repository the log was built from
  TARGET_SHA | SHA | GITHUB_SHA               commit that was built
  WORKFLOW_REPO | REPO | GITHUB_REPOSITORY    repository hosting the workflow run
  WORKFLOW_RUN_ID | RUN_ID | GITHUB_RUN_ID    run id, for the link to the run
  WORKFLOW | GITHUB_WORKFLOW                  workflow name, for the headline
  SUCCESS                                     "true" if the build step succeeded
  INFO                                        anything but "false" reports info messages
  GITHUB_STEP_SUMMARY                         file to append the job summary to

Usage: zulip_build_report.py LOGFILE > "$GITHUB_OUTPUT"

This is the successor of `zulip_build_report.sh`, with the same calling convention.
It targets Python 3.8+ (standard library only), since the self-hosted runners' Python
version is not pinned.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

UNATTRIBUTED = "(not attributed to a linter)"

MESSAGE_RE = re.compile(r"^(error|warning|info): (.*)$")
POSITION_RE = re.compile(r"^(\S+?):(\d+):(\d+): (.*)$")
NOTE_RE = re.compile(r"This linter can be disabled with `set_option (\S+) false`")
# Lines Lake prints between messages: build progress and the final status.
SKIP_PREFIXES = ("✔ ", "⚠ ", "✖ ", "ℹ ", "trace: ", "Build completed", "Some required builds")


@dataclass
class Message:
    severity: str
    file: Optional[str]
    line: Optional[int]
    col: Optional[int]
    text: str
    linter: Optional[str] = None

    @property
    def first_line(self) -> str:
        return self.text.split("\n", 1)[0]

    @property
    def is_panic(self) -> bool:
        return self.severity == "info" and "PANIC at " in self.text


def parse_build_log(lines: Iterable[str]) -> List[Message]:
    """Split a `lake build` log into messages, keeping continuation lines.

    A message starts on an `error:`/`warning:`/`info:` line and runs until the next
    message or the next build-progress line. Lines before the first message are ignored.
    """
    messages: List[Message] = []
    current: Optional[List[str]] = None  # lines of the message being built

    def flush() -> None:
        if current is not None:
            while len(current) > 1 and current[-1] == "":
                current.pop()
            messages[-1].text = "\n".join(current)

    for raw in lines:
        line = raw.rstrip("\n")
        m = MESSAGE_RE.match(line)
        if m:
            flush()
            severity, rest = m.group(1), m.group(2)
            p = POSITION_RE.match(rest)
            if p:
                msg = Message(severity, p.group(1), int(p.group(2)), int(p.group(3)), p.group(4))
            else:
                msg = Message(severity, None, None, None, rest)
            messages.append(msg)
            current = [msg.text]
        elif line.startswith(SKIP_PREFIXES):
            flush()
            current = None
        elif current is not None:
            current.append(line)
    flush()
    return messages


def classify(messages: List[Message]) -> List[Message]:
    """Attribute each message to a linter via the `set_option ... false` note."""
    for msg in messages:
        found = NOTE_RE.search(msg.text)
        msg.linter = found.group(1) if found else UNATTRIBUTED
    return messages


@dataclass
class ReportContext:
    target_repo: str
    target_sha: str
    workflow_repo: str
    run_id: str
    workflow_name: str
    success: bool
    show_info: bool

    @property
    def run_url(self) -> str:
        return f"https://github.com/{self.workflow_repo}/actions/runs/{self.run_id}"

    @property
    def target_url(self) -> str:
        return f"https://github.com/{self.target_repo}"


def severity_counts(messages: List[Message]) -> "OrderedDict[str, int]":
    """Counts per severity, in the order the shell script printed them.

    Panics are info messages, but are counted under "Panics" only.
    """
    counts = OrderedDict()  # type: OrderedDict[str, int]
    panics = sum(1 for m in messages if m.is_panic)
    errors = sum(1 for m in messages if m.severity == "error")
    warnings = sum(1 for m in messages if m.severity == "warning")
    infos = sum(1 for m in messages if m.severity == "info" and not m.is_panic)
    for label, n in (("Panics", panics), ("Errors", errors), ("Warnings", warnings), ("Info messages", infos)):
        if n:
            counts[label] = n
    return counts


def linter_table(messages: List[Message], show_info: bool) -> List[Tuple[str, int, int]]:
    """Per-linter (warnings, infos) counts, largest first, unattributed always last."""
    counts: Dict[str, List[int]] = {}
    for m in messages:
        if m.severity == "error" or m.is_panic:
            continue
        if m.severity == "info" and not show_info:
            continue
        row = counts.setdefault(m.linter or UNATTRIBUTED, [0, 0])
        row[0 if m.severity == "warning" else 1] += 1
    named = sorted(
        ((name, w, i) for name, (w, i) in counts.items() if name != UNATTRIBUTED),
        key=lambda r: (-(r[1] + r[2]), r[0]),
    )
    unattributed = counts.get(UNATTRIBUTED, [0, 0])
    return named + [(UNATTRIBUTED, unattributed[0], unattributed[1])]


def _description_counts(messages: List[Message]) -> List[Tuple[int, str]]:
    """(count, first line) pairs ordered like the shell's `sort | uniq -c | sort -bgr`.

    That is: count descending, ties in reverse byte order of the text (`-r` also
    reverses sort's last-resort comparison).
    """
    counts: Dict[str, int] = {}
    for m in messages:
        counts[m.first_line] = counts.get(m.first_line, 0) + 1
    rows = sorted(counts.items(), key=lambda r: r[0].encode("utf-8"), reverse=True)
    return sorted(((n, d) for d, n in rows), key=lambda r: -r[0])


def _spoiler_table(title: str, column: str, rows: List[Tuple[int, str]]) -> str:
    out = [f"```spoiler {title}", f"| | {column} |", "| ---: | --- |"]
    out += [f"| {n} | {d} |" for n, d in rows]
    out += ["```", ""]
    return "\n".join(out) + "\n"


def render_zulip(messages: List[Message], ctx: ReportContext) -> str:
    """The Zulip message: headline, severity counts, per-linter table, error/panic tables."""
    icon, ended = ("✅", "succeeded") if ctx.success else ("❌", "failed")
    head = (
        f"{icon} {ctx.workflow_name} run on [{ctx.target_repo}]({ctx.target_url}) "
        f"(commit [{ctx.target_sha}]({ctx.target_url}/commit/{ctx.target_sha}))"
    )
    counts = severity_counts(messages)
    if not counts:
        return f"{head} [{ended} without messages]({ctx.run_url}).\n"

    out = [f"{head} [{ended} with messages]({ctx.run_url}):\n"]
    out.append("".join(f"\n* {label}: {n}" for label, n in counts.items()) + "\n\n")

    rows = linter_table(messages, ctx.show_info)
    if any(w + i for _, w, i in rows):
        if ctx.show_info:
            out.append("| | Linter | Warnings | Info |\n| ---: | --- | ---: | ---: |\n")
            out += [f"| | {name} | {w} | {i} |\n" for name, w, i in rows]
        else:
            out.append("| | Linter | Warnings |\n| ---: | --- | ---: |\n")
            out += [f"| | {name} | {w} |\n" for name, w, _ in rows]
        out.append(f"\nFull per-linter tables, with source links, are in the [job summary]({ctx.run_url}).\n\n")

    panics = [m for m in messages if m.is_panic]
    if panics:
        out.append(_spoiler_table("Panic counts", "Panic description", _description_counts(panics)))
    errors = [m for m in messages if m.severity == "error"]
    if errors:
        out.append(_spoiler_table("Error counts", "Error description", _description_counts(errors)))
    return "".join(out)
