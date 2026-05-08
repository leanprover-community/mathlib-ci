#!/usr/bin/env bash
# Validate the downstream names parsed from a `/check-downstream` comment,
# then resolve the PR's merge ref.
#
# Inputs (CLI):
#   --names <comma-separated list, or "all", or "all@lkg">
#   --author-association <issue_comment.author_association value>
#   --output <path to $GITHUB_OUTPUT or another writable file>
#
# Inputs (env):
#   PR_NUMBER          — PR number on leanprover-community/mathlib4
#   GITHUB_REPOSITORY  — owner/repo of the calling workflow
#   GH_TOKEN / GITHUB_TOKEN — used by `gh api` for PR metadata
#   INVENTORY_URL      — (optional) override the inventory URL for testing
#
# Outputs (appended to --output as KEY=VALUE):
#   resolved_names — comma-separated, normalised, validated downstream names,
#                    preserving any `@lkg` mode suffix supplied by the user
#                    (or applied wholesale by `all@lkg`)
#   head_repo      — owner/repo of the PR head (differs from base on forks)
#   head_sha       — HEAD SHA of the PR branch
#   merge_sha      — resolved SHA of refs/pull/N/merge (the would-be-merged tree)
#
# Comment grammar
# ---------------
# Per-name optional suffix `@lkg` selects rebase-onto-LKG mode for that
# downstream — see docs/internal/pr-validation-workflow.md in
# downstream-reports for the full design.
#
#     /check-downstream FLT@lkg, Toric        # FLT in LKG mode, Toric in merge mode
#     /check-downstream all                    # every enabled downstream, merge mode
#     /check-downstream all@lkg                # every enabled downstream, LKG mode
#
# Authorization model:
#   `all` and `all@lkg` are gated to OWNER and MEMBER only. COLLABORATORs
#   must enumerate names explicitly. The calling workflow has already gated
#   execution to OWNER/MEMBER/COLLABORATOR, so this is purely narrowing
#   `all`.
#
# Exits non-zero with a `::error::` annotation on any failure.

set -euo pipefail

# Allow tests to point at a local or branch copy of the inventory.
INVENTORY_URL="${INVENTORY_URL:-https://raw.githubusercontent.com/leanprover-community/downstream-reports/main/ci/inventory/downstreams.json}"

NAMES=""
ASSOC=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --names) NAMES="$2"; shift 2 ;;
    --author-association) ASSOC="$2"; shift 2 ;;
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

# ---- Resolve names -----------------------------------------------------------
# We accept three high-level shapes:
#   1) "all"      — every enabled downstream, merge mode
#   2) "all@lkg"  — every enabled downstream, LKG mode (suffix applied wholesale)
#   3) a comma-separated list, where each entry may carry an optional `@lkg`
#      suffix.

NAMES_TRIMMED="$(echo "$NAMES" | tr -d '[:space:]')"

if [ "$NAMES_TRIMMED" = "all" ] || [ "$NAMES_TRIMMED" = "all@lkg" ]; then
  case "$ASSOC" in
    OWNER|MEMBER) ;;
    *)
      echo "::error::author_association=$ASSOC is not allowed to use 'all' / 'all@lkg'; enumerate names instead"
      exit 1
      ;;
  esac
  ALL_NAMES="$(jq -r '[.downstreams[] | select(.enabled // true) | .name] | join(",")' \
                  "$INVENTORY")"
  if [ -z "$ALL_NAMES" ]; then
    echo "::error::inventory has no enabled downstreams"
    exit 1
  fi
  if [ "$NAMES_TRIMMED" = "all@lkg" ]; then
    # Tack `@lkg` onto every name.
    RESOLVED="$(echo "$ALL_NAMES" | awk -F',' '{
      for (i=1; i<=NF; i++) printf "%s%s@lkg", (i>1 ? "," : ""), $i
    }')"
  else
    RESOLVED="$ALL_NAMES"
  fi
else
  # Strip whitespace, drop empties, deduplicate while preserving order.
  RESOLVED="$(echo "$NAMES" | tr ',' '\n' \
                | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
                | awk 'NF && !seen[$0]++' \
                | paste -sd',' -)"
  if [ -z "$RESOLVED" ]; then
    echo "::error::no downstream names provided"
    exit 1
  fi
  # Validate each name against the inventory (case-sensitive). Tokens may
  # carry an optional `@lkg` suffix; only the bare name is matched against
  # the inventory, but the suffix is preserved verbatim in $RESOLVED.
  KNOWN="$(jq -r '.downstreams[].name' "$INVENTORY" | sort -u)"
  UNKNOWN=""
  BAD_SUFFIX=""
  IFS=',' read -ra REQ <<< "$RESOLVED"
  for token in "${REQ[@]}"; do
    bare="${token%%@*}"
    suffix="${token#"$bare"}"   # "" or "@<mode>"
    if [ -n "$suffix" ] && [ "$suffix" != "@lkg" ]; then
      BAD_SUFFIX="${BAD_SUFFIX:+$BAD_SUFFIX, }$token"
      continue
    fi
    if ! grep -Fxq "$bare" <<< "$KNOWN"; then
      UNKNOWN="${UNKNOWN:+$UNKNOWN, }$bare"
    fi
  done
  if [ -n "$BAD_SUFFIX" ]; then
    echo "::error::unknown mode suffix on: $BAD_SUFFIX (only @lkg is supported)"
    exit 1
  fi
  if [ -n "$UNKNOWN" ]; then
    echo "::error::unknown downstream(s): $UNKNOWN"
    exit 1
  fi
fi

# ---- Resolve the PR merge ref ------------------------------------------------
# We use the merge_commit_sha from the PR API rather than refs/pull/N/merge
# because the ref can be stale between pushes; the API field reflects the
# current computed merge tree. GitHub sets mergeable=null while it computes
# mergeability, so null is treated as an error (caller should retry).

PR_JSON="$(gh api "/repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER")"
HEAD_REPO="$(jq -r '.head.repo.full_name' <<< "$PR_JSON")"
HEAD_SHA="$(jq -r '.head.sha' <<< "$PR_JSON")"
MERGEABLE="$(jq -r '.mergeable' <<< "$PR_JSON")"
MERGE_COMMIT_SHA="$(jq -r '.merge_commit_sha' <<< "$PR_JSON")"

if [ "$MERGEABLE" = "false" ] || [ "$MERGE_COMMIT_SHA" = "null" ] \
   || [ -z "$MERGE_COMMIT_SHA" ]; then
  echo "::error::PR $PR_NUMBER has no mergeable merge ref (mergeable=$MERGEABLE); the PR may have conflicts or mergeability is still being computed"
  exit 1
fi

{
  echo "resolved_names=$RESOLVED"
  echo "head_repo=$HEAD_REPO"
  echo "head_sha=$HEAD_SHA"
  echo "merge_sha=$MERGE_COMMIT_SHA"
} >> "$OUTPUT"

echo "validated downstreams: $RESOLVED"
echo "head: $HEAD_REPO@$HEAD_SHA"
echo "merge: $MERGE_COMMIT_SHA"
