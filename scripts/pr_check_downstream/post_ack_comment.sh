#!/usr/bin/env bash
# Post the acknowledgement comment on a mathlib4 PR, confirming that
# downstream validation has been triggered.
#
# Inputs (env, all required unless noted):
#   GITHUB_TOKEN      — token with issues:write on leanprover-community/mathlib4
#   GITHUB_REPOSITORY — owner/repo of the calling workflow
#   PR_NUMBER         — PR number
#   DOWNSTREAMS       — comma-separated validation entries as resolved by
#                       validate_names.sh; each may carry an @<rev> suffix
#                       and/or a trailing --merge-branch flag.
#   MERGE_SHA         — resolved merge SHA (displayed as a 7-char short SHA)
#   GITHUB_SERVER_URL — (standard Actions var) used to build the run link
#   GITHUB_RUN_ID     — (standard Actions var) used to build the run link
#
# One ack comment is POSTed per dispatch. Multiple `!downstream-check`
# comments on the same PR therefore leave their own ack lines in the
# conversation — each one pinned by its own dispatch run link — so the
# audit trail of what got triggered survives. The matching result
# comment is also POSTed fresh per dispatch by downstream-reports'
# post_results.py.

set -euo pipefail

: "${GITHUB_TOKEN:?GITHUB_TOKEN must be set}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"
: "${PR_NUMBER:?PR_NUMBER must be set}"
: "${DOWNSTREAMS:?DOWNSTREAMS must be set}"
: "${MERGE_SHA:?MERGE_SHA must be set}"

SHORT_SHA="${MERGE_SHA:0:7}"

# Render each entry as a backtick-quoted, comma-separated token. Entries
# can carry `@<rev>` suffixes and a trailing `--merge-branch` flag; both
# are kept inside the backticks so the ack mirrors the user's input.
# Strip any stray backticks from the rev portion before wrapping — git
# allows them in refnames and a backtick mid-token would close the
# Markdown code span and let user-controlled text render unquoted.
ENTRIES_RENDERED="$(echo "$DOWNSTREAMS" | tr ',' '\n' \
                      | tr -d '`' \
                      | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
                      | awk 'NF { printf "%s`%s`", (i++ ? ", " : ""), $0 } END { print "" }')"

RUN_URL=""
if [ -n "${GITHUB_SERVER_URL:-}" ] && [ -n "${GITHUB_RUN_ID:-}" ]; then
  RUN_URL="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
fi

BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT

{
  echo "**Downstream validation triggered**"
  echo
  echo "Validating this PR (merge ref \`$SHORT_SHA\`) against: $ENTRIES_RENDERED."
  echo
  echo "Each entry runs in LKG mode by default (the PR's commits are"
  echo "cherry-picked onto the downstream's last-known-good mathlib commit);"
  echo "entries with \`--merge-branch\` are tested against the PR's merge tree"
  echo "instead. A single follow-up comment with the full result table"
  echo "will land here when the run finishes."
  if [ -n "$RUN_URL" ]; then
    echo
    echo "Dispatch: [run]($RUN_URL)"
  fi
} > "$BODY_FILE"

API="https://api.github.com/repos/$GITHUB_REPOSITORY/issues"
AUTH_HEADER="Authorization: token $GITHUB_TOKEN"
JSON_HEADER="Accept: application/vnd.github+json"

# `jq -Rs` reads stdin as a raw string and wraps it in a JSON object.
BODY_JSON="$(jq -Rs '{body: .}' < "$BODY_FILE")"

echo "posting ack comment on PR $PR_NUMBER"
curl -sSf -X POST \
  -H "$AUTH_HEADER" -H "$JSON_HEADER" \
  -d "$BODY_JSON" \
  "$API/$PR_NUMBER/comments" >/dev/null
