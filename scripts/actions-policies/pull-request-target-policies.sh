#!/usr/bin/env bash
# Maintain the organization Actions policies that allow `pull_request_target` for the workflows
# that need it. From 2026-11-02 GitHub blocks `pull_request_target` in public repositories unless
# an Actions event policy allows it; these policies allow it for exactly the workflow files listed
# below, and leave GitHub's default block in place for every other workflow and repository.
#
#   ./pull-request-target-policies.sh                  # dry run: print the policy JSON per repo
#   ./pull-request-target-policies.sh --apply          # create (disabled) or update each policy,
#                                                      # keeping the enforcement it already has
#   ./pull-request-target-policies.sh --apply --enforce active --repos blog
#                                                      # create or update, and set enforcement
#   ./pull-request-target-policies.sh --check          # list default-branch workflows triggered by
#                                                      # pull_request_target that no policy covers,
#                                                      # and listed paths that no longer exist
#
# There is one policy per repository, named "pull_request_target allowlist: <repo>", applying to
# that repository and the listed workflow paths only. Its event rule is an allowlist: a matched
# workflow may run only on the listed events, so the list must include every event those files
# use (push, schedule, ...), not just pull_request_target. Workflows not matched by any policy
# are unaffected, apart from GitHub's default block on pull_request_target.
#
# Rollout: --apply creates new policies disabled. Activate them one repository at a time with
# `--apply --enforce active --repos <repo>`, then check
# https://github.com/organizations/leanprover-community/settings/actions/policies (Policy insights).
#
# Requires: gh (authenticated as an org owner), jq.

set -euo pipefail

ORG=leanprover-community
NAME_PREFIX="pull_request_target allowlist: "

APPLY=0; CHECK=0; ENFORCE=""; ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1;;
    --check) CHECK=1;;
    --enforce) ENFORCE="$2"; shift;;
    --repos) ONLY="$2"; shift;;
    -h|--help) sed -n '2,27p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac; shift
done
case "$ENFORCE" in ""|active|disabled) ;; *) echo "--enforce must be active or disabled" >&2; exit 2;; esac

# repo | workflow files (comma-separated, under .github/workflows/) | allowed events
# The events are the union of the `on:` triggers of the listed files.
CONFIG=$(cat <<'CFG'
mathlib4|build_fork.yml,PR_summary.yml,add_label_from_diff.yaml,check_pr_titles.yaml,label_new_contributor.yml,zulip_emoji_reconcile.yml|pull_request_target,schedule,workflow_dispatch,workflow_run
mathlib4-nightly-testing|build_fork.yml,PR_summary.yml,add_label_from_diff.yaml,check_pr_titles.yaml,label_new_contributor.yml,zulip_emoji_reconcile.yml|pull_request_target,schedule,workflow_dispatch,workflow_run
batteries|labels-from-status.yml|pull_request_target
physlib|PRMessage.yml,add_label_from_diff.yaml,pr_size_label.yaml|pull_request_target,push,workflow_dispatch
blog|message_zulip_on_prs.yml|pull_request_target
leanprover-community.github.io|message_zulip_on_prs.yml,zulip_emoji_reconcile.yml|pull_request_target,schedule,workflow_dispatch,workflow_run
CFG
)

# The top-level `on:` block of a workflow file (stdin), without comments, so that mentions of
# github.event_name == 'pull_request_target' in job bodies are not counted as triggers.
on_block() { sed 's/#.*//' | awk '/^"?on"?:/ {inon=1; print; next} inon && /^[^[:space:]]/ {inon=0} inon'; }

selected() { [ -z "$ONLY" ] || grep -qx "$1" <<<"${ONLY//,/$'\n'}"; }

build_policy() {
  local repo="$1" files="$2" events="$3" enforcement="$4"
  jq -n --arg name "$NAME_PREFIX$repo" --arg repo "$repo" --arg files "$files" \
    --arg events "$events" --arg enforcement "$enforcement" '
    {
      name: $name,
      enforcement: $enforcement,
      conditions: {
        repository_name: { include: [$repo], exclude: [] },
        workflow_path: {
          include: ($files | split(",") | map(".github/workflows/" + .)),
          exclude: []
        }
      },
      rules: [
        { type: "restrict_action_events",
          parameters: { allowed_events: ($events | split(",")) } }
      ]
    }'
}

if [ "$CHECK" = 1 ]; then
  while IFS='|' read -r repo files events; do
    [ -z "$repo" ] && continue
    selected "$repo" || continue
    db=$(gh api "/repos/$ORG/$repo" --jq .default_branch)
    present=$(gh api "/repos/$ORG/$repo/contents/.github/workflows?ref=$db" --jq '.[].name')
    for f in ${files//,/ }; do
      grep -qx "$f" <<<"$present" || echo "$repo: listed $f no longer exists"
    done
    for f in $present; do
      grep -qx "$f" <<<"${files//,/$'\n'}" && continue
      if gh api -H 'Accept: application/vnd.github.raw' \
          "/repos/$ORG/$repo/contents/.github/workflows/$f?ref=$db" | on_block | grep -q pull_request_target; then
        echo "$repo: $f is triggered by pull_request_target but is not listed"
      fi
    done
  done <<<"$CONFIG"
  echo "(--check only looks at repositories in CONFIG.)"
  exit 0
fi

existing_json=""
[ "$APPLY" = 1 ] && existing_json=$(gh api "/orgs/$ORG/actions/policies" --jq '.policies')

while IFS='|' read -r repo files events; do
  [ -z "$repo" ] && continue
  selected "$repo" || continue
  echo "=== $ORG/$repo ==="
  if [ "$APPLY" = 0 ]; then
    build_policy "$repo" "$files" "$events" "${ENFORCE:-disabled}" | jq .
    continue
  fi
  id=$(jq -r --arg n "$NAME_PREFIX$repo" '.[] | select(.name == $n) | .id' <<<"$existing_json")
  if [ -n "$id" ]; then
    current=$(jq -r --arg n "$NAME_PREFIX$repo" '.[] | select(.name == $n) | .enforcement' <<<"$existing_json")
    build_policy "$repo" "$files" "$events" "${ENFORCE:-$current}" |
      gh api -X PUT "/orgs/$ORG/actions/policies/$id" --input - \
        --jq '"updated: \(.name) [\(.enforcement)] id=\(.id)"'
  else
    build_policy "$repo" "$files" "$events" "${ENFORCE:-disabled}" |
      gh api -X POST "/orgs/$ORG/actions/policies" --input - \
        --jq '"created: \(.name) [\(.enforcement)] id=\(.id)"'
  fi
done <<<"$CONFIG"
