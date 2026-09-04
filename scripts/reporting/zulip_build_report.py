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
