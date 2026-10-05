"""Command-line entry: read the log, write the Zulip block and the job summary.

Usage: zulip_build_report.py LOGFILE > "$GITHUB_OUTPUT"

stdout carries a `zulip-message<<DELIM ... DELIM` block for `GITHUB_OUTPUT`; stderr
carries the line counts the shell script printed; the job summary is appended to
`GITHUB_STEP_SUMMARY` when that variable is set (and the Zulip message only points to
the summary when it is).
"""

from __future__ import annotations

import os
import sys
import uuid
from contextlib import nullcontext
from typing import List

from .context import context_from_env
from .lake_log import classify, parse_build_log, severity_counts
from .summary import SUMMARY_LIMIT, render_summary
from .zulip import render_zulip


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} LOGFILE", file=sys.stderr)
        return 2
    # The headline carries an emoji; don't let a runner's locale decide whether it can
    # be written (a failure here would leave `GITHUB_OUTPUT` empty).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    with open(argv[1], encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    ctx = context_from_env(os.environ)
    messages = classify(parse_build_log(lines))

    # Progress on stderr, in the shell script's wording.
    filtered = [line for line in lines if not line.startswith(("✔", "trace: "))]
    print(f"{len(filtered)} lines of output", file=sys.stderr)
    counts = severity_counts(messages)
    for label, noun in (("Panics", "panic"), ("Errors", "errors"), ("Warnings", "warnings"), ("Info messages", "info")):
        if label in counts:
            print(f"{counts[label]} lines of {noun}", file=sys.stderr)

    summary = ""
    if ctx.summary_path:
        # GitHub's 1 MiB cap is on the whole file for the step, and exceeding it drops
        # the summary entirely, so budget for whatever earlier commands already wrote.
        existing = os.path.getsize(ctx.summary_path) if os.path.exists(ctx.summary_path) else 0
        summary = render_summary(messages, ctx, limit=max(0, SUMMARY_LIMIT - existing))
        if not summary:
            print(f"job summary not written: {existing} bytes already in {ctx.summary_path}, "
                  f"limit is {SUMMARY_LIMIT}", file=sys.stderr)

    # The summary file is opened before anything goes to stdout: the Zulip message
    # points to the summary, so an unwritable path must fail the step, not post a link.
    with (open(ctx.summary_path, "a", encoding="utf-8") if ctx.summary_path else nullcontext()) as f:
        delimiter = str(uuid.uuid4())
        sys.stdout.write(f"zulip-message<<{delimiter}\n{render_zulip(messages, ctx)}{delimiter}\n")
        if summary:
            f.write(summary)
    return 0
