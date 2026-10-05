#!/usr/bin/env python3
"""Summarise a `lake build` log for Zulip and the GitHub job summary.

Entry point; see build_report/ for the implementation. Reads the text log of a
`lake build` (typically the weekly linting run), groups the messages by the linter that
produced them, and emits:

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

import sys

from build_report.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
