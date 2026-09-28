"""Parse a `lake build` log into messages and attribute them to linters.

Lake prints each Lean message as `severity: file:line:col: text`, possibly followed by
continuation lines, interleaved with build-progress lines (`✔ [12/345] Built ...`).
`parse_build_log` turns that into `Message` records; `classify` fills in `Message.linter`
from the note that `Lean.Linter.logLint` appends to every linter message,
"This linter can be disabled with `set_option linter.X false`". Messages without that
note are attributed to `UNATTRIBUTED`.

This is the only module that knows Lake's output format.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

UNATTRIBUTED = "(not attributed to a linter)"

MESSAGE_RE = re.compile(r"^(error|warning|info): (.*)$")
POSITION_RE = re.compile(r"^(\S+?):(\d+):(\d+): (.*)$")
# `Lean.Linter.logLint` ends with "... `set_option linter.X false`"; Mathlib's
# `logLint0Disable` variant (e.g. `linter.haveLet`, `linter.style.longFile`) ends in `0`.
NOTE_RE = re.compile(r"This linter can be disabled with `set_option (\S+) (?:false|0)`")
# Lines Lake prints between messages. Progress lines look like `✔ [12/345] Built X (1.2s)`
# (also ⚠, ✖, ℹ); the epilogue is `Build completed successfully (N jobs).` or
# `Some required targets logged failures:` followed by `- Module` lines.
PROGRESS_RE = re.compile(r"^[✔⚠✖ℹ] \[\d+/\d+\]")
EPILOGUE_RE = re.compile(r"^(Build completed|Some required \w+ logged failures)")
PANIC_PREFIX = "PANIC at "


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
    def panic_lines(self) -> List[str]:
        """The `PANIC at ...` lines in this message.

        Lean panics go to stderr, which Lake relays as one unpositioned `info: stderr:`
        message with the panic lines after it, so one message can carry several panics.
        """
        if self.severity != "info":
            return []
        return [line for line in self.text.split("\n") if line.startswith(PANIC_PREFIX)]

    @property
    def is_panic(self) -> bool:
        return bool(self.panic_lines)


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
        line = raw.rstrip("\r\n")
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
        elif PROGRESS_RE.match(line) or EPILOGUE_RE.match(line) or line.startswith("trace: "):
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


def severity_counts(messages: List[Message]) -> "OrderedDict[str, int]":
    """Counts per severity, in the order the shell script printed them.

    Panics are info messages, but are counted under "Panics" only.
    """
    counts = OrderedDict()  # type: OrderedDict[str, int]
    panics = sum(len(m.panic_lines) for m in messages)
    errors = sum(1 for m in messages if m.severity == "error")
    warnings = sum(1 for m in messages if m.severity == "warning")
    infos = sum(1 for m in messages if m.severity == "info" and not m.is_panic)
    for label, n in (("Panics", panics), ("Errors", errors), ("Warnings", warnings), ("Info messages", infos)):
        if n:
            counts[label] = n
    return counts


def linter_table(messages: List[Message], show_info: bool) -> List[Tuple[str, int, int]]:
    """Per-linter (warnings, infos) counts, largest first, unattributed always last.

    Errors and panics are not linter output and are left out. This ordering is used both
    for the Zulip table and for the order of sections in the job summary.
    """
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
