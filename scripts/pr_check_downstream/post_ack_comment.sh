#!/usr/bin/env bash
# Post (or edit in place) the acknowledgement comment on a mathlib4 PR,
# confirming that downstream validation has been triggered.
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
# The comment is identified by a hidden HTML marker so re-triggers edit the
# same comment rather than stacking new ones.

set -euo pipefail

: "${GITHUB_TOKEN:?GITHUB_TOKEN must be set}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"
: "${PR_NUMBER:?PR_NUMBER must be set}"
: "${DOWNSTREAMS:?DOWNSTREAMS must be set}"
: "${MERGE_SHA:?MERGE_SHA must be set}"

MARKER="<!-- pr-check-downstream:ack -->"
SHORT_SHA="${MERGE_SHA:0:7}"

# Render each entry as a backtick-quoted, comma-separated token. Entries
# can carry `@<rev>` suffixes and a trailing `--merge-branch` flag; both
# are kept inside the backticks so the ack mirrors the user's input.
ENTRIES_RENDERED="$(echo "$DOWNSTREAMS" | tr ',' '\n' \
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
  echo
  echo "$MARKER"
} > "$BODY_FILE"

API="https://api.github.com/repos/$GITHUB_REPOSITORY/issues"
AUTH_HEADER="Authorization: token $GITHUB_TOKEN"
JSON_HEADER="Accept: application/vnd.github+json"

# Check the first page of comments for an existing ack (marker is unique per PR).
EXISTING_ID="$(curl -sSf -H "$AUTH_HEADER" -H "$JSON_HEADER" \
                "$API/$PR_NUMBER/comments?per_page=100" \
              | jq --arg marker "$MARKER" -r \
                  '.[] | select(.body | contains($marker)) | .id' \
              | head -n1)"

# `jq -Rs` reads stdin as a raw string and wraps it in a JSON object.
BODY_JSON="$(jq -Rs '{body: .}' < "$BODY_FILE")"

if [ -n "$EXISTING_ID" ]; then
  echo "updating existing ack comment $EXISTING_ID"
  curl -sSf -X PATCH \
    -H "$AUTH_HEADER" -H "$JSON_HEADER" \
    -d "$BODY_JSON" \
    "$API/comments/$EXISTING_ID" >/dev/null
else
  echo "posting new ack comment on PR $PR_NUMBER"
  curl -sSf -X POST \
    -H "$AUTH_HEADER" -H "$JSON_HEADER" \
    -d "$BODY_JSON" \
    "$API/$PR_NUMBER/comments" >/dev/null
fi
