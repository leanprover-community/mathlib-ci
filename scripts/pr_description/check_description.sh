#!/usr/bin/env bash
# Check that a PR description begins with the literal phrase "This PR".
#
# Usage:
#   ./scripts/pr_description/check_description.sh <path-to-body-file>
#   ./scripts/pr_description/check_description.sh -        # read body from stdin
#
# Exit codes:
#   0 - first non-blank line begins with "This PR"
#   1 - description is empty or first non-blank line does not begin with "This PR"
#   2 - usage error
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

# First non-blank line, stripped of leading whitespace.
first_line=$(printf '%s\n' "$body" | awk 'NF { sub(/^[[:space:]]+/, ""); print; exit }')

if [[ "$first_line" == This\ PR* ]]; then
  exit 0
fi

cat >&2 <<'EOF'
PR description must begin with a sentence starting "This PR ...".

For example: "This PR adds ...", "This PR fixes ...", "This PR refactors ...".
That first sentence is what gets incorporated into the release notes, so it
needs to stand on its own as a summary of what the PR does.

It's nice if the description goes on to describe the changes, their
motivation, and any advice for downstream users affected by the PR --
but the bare minimum is one sentence saying what the PR does.
EOF
exit 1
