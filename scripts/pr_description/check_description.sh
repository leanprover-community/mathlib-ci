#!/usr/bin/env bash
# Check that a PR description begins with a sentence starting "This PR ".
#
# Usage:
#   ./scripts/pr_description/check_description.sh <path-to-body-file>
#   ./scripts/pr_description/check_description.sh -        # read body from stdin
#
# Exit codes:
#   0 - the first content line begins with "This PR " (a literal space after `PR`)
#   1 - description is empty or the first content line does not begin with "This PR "
#   2 - usage error
#
# Leading blank lines and HTML comments (single- or multi-line `<!-- ... -->`,
# possibly with whitespace around them) are skipped before the check, so a PR
# template that opens with `<!-- ... -->` on its own line(s) does not trip the
# check. The first content line is then matched as-is (after stripping a
# trailing CR from CRLF line endings) -- leading whitespace on a content line
# is intentionally NOT stripped, so an indented markdown code block beginning
# with "This PR" does not pass.
#
# The companion workflow that invokes this script should run only on open
# pull requests, so this check does not fire on closed/merged PRs (whose
# bodies can be rewritten by bots after merge).
#
# The failure message printed on stderr is also the message intended for the
# sticky PR comment, so keep it short and self-explanatory.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <path-to-body-file | ->" >&2
  exit 2
fi

if [[ "$1" == "-" ]]; then
  body=$(cat)
else
  body=$(cat -- "$1")
fi

first_line=$(printf '%s\n' "$body" | awk '
  BEGIN { in_comment = 0 }
  {
    sub(/\r$/, "")
    line = $0

    while (1) {
      if (in_comment) {
        idx = index(line, "-->")
        if (idx == 0) { line = ""; break }
        line = substr(line, idx + 3)
        in_comment = 0
      }
      # Peek at the line ignoring leading whitespace: if blank, drop the line;
      # if it starts with an HTML comment, consume the comment in place.
      peek = line
      sub(/^[[:space:]]+/, "", peek)
      if (peek == "") { line = ""; break }
      if (substr(peek, 1, 4) != "<!--") break
      sub(/^[[:space:]]*<!--/, "", line)
      idx = index(line, "-->")
      if (idx == 0) {
        in_comment = 1
        line = ""
      } else {
        line = substr(line, idx + 3)
      }
    }

    if (line == "" || line ~ /^[[:space:]]*$/) next
    print line
    exit
  }
')

if [[ "$first_line" == "This PR "* ]]; then
  exit 0
fi

cat >&2 <<'EOF'
PR description must start with a sentence beginning "This PR ...".

For example: "This PR adds ...", "This PR fixes ...", "This PR refactors ...".
That first sentence is what gets incorporated into the release notes, so it
needs to stand on its own as a summary of what the PR does.

It's nice if the description goes on to describe the changes, their
motivation, and any advice for downstream users affected by the PR --
but the bare minimum is one sentence saying what the PR does.
EOF
exit 1
