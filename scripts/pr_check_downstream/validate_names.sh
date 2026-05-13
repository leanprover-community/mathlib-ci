#!/usr/bin/env bash
# Validate the downstream entries parsed from a `!downstream-check` comment,
# then resolve the PR's merge ref.
#
# Comment grammar (each comma-separated entry):
#
#   <name>[@<rev>] [--merge-branch]
#
# - <name>          must match an inventory entry (case-sensitive).
# - @<rev>          optional. Any git refspec (branch / tag / commit SHA)
#                   for the downstream's checkout; defaults to the
#                   inventory's default_branch when absent.
# - --merge-branch  optional, per entry. Flips that single entry from the
#                   default LKG mode to merge mode (the dispatched workflow
#                   then tests it against the PR's merge tree instead of
#                   cherry-picking onto the LKG).
#
# Inputs (CLI):
#   --names   <list>          (the comma-separated entries verbatim)
#   --output  <path>          ($GITHUB_OUTPUT or a writable file)
#
# Inputs (env):
#   PR_NUMBER         — PR number on leanprover-community/mathlib4
#   GITHUB_REPOSITORY — owner/repo of the calling workflow
#   GH_TOKEN / GITHUB_TOKEN — used by `gh api` for PR metadata
#   INVENTORY_URL     — (optional) override the inventory URL for testing
#
# Outputs (appended to --output as KEY=VALUE):
#   resolved_names — normalised comma-separated list of validated entries,
#                    preserving each entry's `@<rev>` suffix and
#                    `--merge-branch` flag verbatim
#   merge_sha      — resolved SHA of refs/pull/N/merge (the would-be-merged
#                    tree). Lives on leanprover-community/mathlib4 even for
#                    fork PRs.
#
# Exits non-zero with a `::error::` annotation on any failure. Authorization
# (OWNER/MEMBER/COLLABORATOR) is gated upstream in the mathlib4 workflow.

set -euo pipefail

# Allow tests to point at a local or branch copy of the inventory.
INVENTORY_URL="${INVENTORY_URL:-https://raw.githubusercontent.com/leanprover-community/downstream-reports/main/ci/inventory/downstreams.json}"

NAMES=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --names) NAMES="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    *) echo "::error::unknown arg: $1" >&2; exit 1 ;;
  esac
done

: "${NAMES:?missing --names}"
: "${OUTPUT:?missing --output}"
: "${PR_NUMBER:?PR_NUMBER must be set}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"

INVENTORY="$(mktemp)"
trap 'rm -f "$INVENTORY"' EXIT

if ! curl -sSfL "$INVENTORY_URL" -o "$INVENTORY"; then
  echo "::error::could not fetch inventory at $INVENTORY_URL"
  exit 1
fi

# ---- Parse and validate entries ---------------------------------------------
#
# Each comma-separated entry is itself whitespace-delimited:
#   <name>[@<rev>] [--merge-branch]
#
# We canonicalise each entry to:
#   <name>[@<rev>][ --merge-branch]
# (single spaces, no leading/trailing whitespace), validate the bare name
# against the inventory, reject unknown flags, and round-trip the result.

KNOWN="$(jq -r '.downstreams[].name' "$INVENTORY" | sort -u)"

RESOLVED_ENTRIES=()
UNKNOWN_NAMES=""
UNKNOWN_FLAGS=""

# Iterate commas. Use IFS to split into an array; do not splat directly since
# entries can contain spaces (we want each comma-delimited entry whole).
IFS=',' read -ra REQ <<< "$NAMES"
for raw in "${REQ[@]}"; do
  # Trim whitespace around the whole entry.
  entry="$(echo "$raw" | sed -E 's/^[[:space:]]+//;s/[[:space:]]+$//')"
  if [ -z "$entry" ]; then
    continue
  fi

  # Split the entry into whitespace-separated tokens: first is the
  # `<name>[@<rev>]`, the rest are flags.
  read -ra parts <<< "$entry"
  name_rev="${parts[0]}"
  flags=("${parts[@]:1}")

  bare="${name_rev%%@*}"
  suffix="${name_rev#"$bare"}"   # "" or "@<rev>"

  if [ -z "$bare" ]; then
    echo "::error::empty downstream name in entry: '$entry'"
    exit 1
  fi

  if ! grep -Fxq "$bare" <<< "$KNOWN"; then
    UNKNOWN_NAMES="${UNKNOWN_NAMES:+$UNKNOWN_NAMES, }$bare"
    continue
  fi

  merge_flag=""
  for f in "${flags[@]}"; do
    case "$f" in
      --merge-branch)
        merge_flag=" --merge-branch"
        ;;
      "")
        ;;
      *)
        UNKNOWN_FLAGS="${UNKNOWN_FLAGS:+$UNKNOWN_FLAGS, }$f"
        ;;
    esac
  done

  RESOLVED_ENTRIES+=("${bare}${suffix}${merge_flag}")
done

if [ -n "$UNKNOWN_NAMES" ]; then
  echo "::error::unknown downstream(s): $UNKNOWN_NAMES"
  exit 1
fi
if [ -n "$UNKNOWN_FLAGS" ]; then
  echo "::error::unknown flag(s): $UNKNOWN_FLAGS (only --merge-branch is supported)"
  exit 1
fi
if [ ${#RESOLVED_ENTRIES[@]} -eq 0 ]; then
  echo "::error::no downstream entries provided"
  exit 1
fi

# Dedup while preserving order (entries with different flags / revs are
# already textually distinct, so a strict string-equality dedup is enough).
RESOLVED="$(printf '%s\n' "${RESOLVED_ENTRIES[@]}" \
              | awk 'NF && !seen[$0]++' \
              | paste -sd',' -)"

# ---- Resolve the PR merge ref ------------------------------------------------
# We use the merge_commit_sha from the PR API rather than refs/pull/N/merge
# because the ref can be stale between pushes; the API field reflects the
# current computed merge tree. GitHub sets mergeable=null while it computes
# mergeability, so null is treated as an error (caller should retry).

PR_JSON="$(gh api "/repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER")"
MERGEABLE="$(jq -r '.mergeable' <<< "$PR_JSON")"
MERGE_COMMIT_SHA="$(jq -r '.merge_commit_sha' <<< "$PR_JSON")"

if [ "$MERGEABLE" = "false" ] || [ "$MERGE_COMMIT_SHA" = "null" ] \
   || [ -z "$MERGE_COMMIT_SHA" ]; then
  echo "::error::PR $PR_NUMBER has no mergeable merge ref (mergeable=$MERGEABLE); the PR may have conflicts or mergeability is still being computed"
  exit 1
fi

{
  echo "resolved_names=$RESOLVED"
  echo "merge_sha=$MERGE_COMMIT_SHA"
} >> "$OUTPUT"

echo "validated entries: $RESOLVED"
echo "merge: $MERGE_COMMIT_SHA"
