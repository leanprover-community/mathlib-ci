#!/usr/bin/env bash
# Apply a baseline default-branch ruleset to Mathlib's dependency repos, the community website
# and the blog.
#
#   ./dep-repo-rulesets.sh              # dry run: print the ruleset JSON per repo
#   ./dep-repo-rulesets.sh --apply      # create (or update, if a ruleset of the same name exists),
#                                       # then delete any classic branch protection on the default
#                                       # branch so the ruleset is the single source of truth
#                                       # (only batteries has classic protection today)
#   ./dep-repo-rulesets.sh --repos batteries,quote4   # restrict to a subset
#
# The ruleset, applied to ~DEFAULT_BRANCH of each repo:
#   - no deletion, no force push
#   - changes only via pull request (0 approvals required, so maintainers can still
#     merge their own PRs immediately; the point is that every change goes through CI
#     and leaves a PR trail, not that a second human signs off)
#   - the repo's existing CI job is a required status check
#   - linear history
#   - squash merges only (the repo settings are switched to squash-only as well)
#   - bypass: repository admins, in "pull_request" mode only. That lets an admin merge a PR
#     with a red check in an emergency; nobody can push directly, not even admins.
#
# Who this changes day-to-day (direct pushes to the default branch seen in recent history):
#   ProofWidgets4:   Wojciech Nawrocki
#   plausible:       Kim Morrison, Henrik Böving
#   LeanSearchClient: Siddhartha Gadgil
# Tell them before applying. batteries, quote4 and import-graph already go through PRs.
# The website's default branch is `lean4` (its `master` is the built site, pushed by the website
# app) and the blog deploys to `deploy`, so neither deploy bot is affected.
#
# Requires: gh (authenticated as an org owner or repo admin), jq.

set -euo pipefail

ORG=leanprover-community
RULESET_NAME="default branch: pull requests only"
GITHUB_ACTIONS_APP_ID=15368   # integration_id for checks produced by GitHub Actions
ADMIN_ROLE_ID=5               # RepositoryRole id for "admin"

APPLY=0; ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1;;
    --repos) ONLY="$2"; shift;;
    -h|--help) sed -n '2,30p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac; shift
done

# repo | required check names (comma-separated) | linear history?
# Check names are the CI job names: the `name:` of the job, or the job id when unnamed.
CONFIG=$(cat <<'CFG'
batteries|Build|true
quote4|Build|true
ProofWidgets4|Build on Ubuntu,Build on Windows|true
plausible|build|true
LeanSearchClient|build|true
import-graph|build|true
leanprover-community.github.io|Build HTML|true
blog|Build HTML|true
CFG
)

build_ruleset() {
  local repo="$1" checks="$2" linear="$3"
  jq -n \
    --arg name "$RULESET_NAME" \
    --arg checks "$checks" \
    --argjson linear "$linear" \
    --argjson app "$GITHUB_ACTIONS_APP_ID" \
    --argjson admin "$ADMIN_ROLE_ID" '
    {
      name: $name,
      target: "branch",
      enforcement: "active",
      conditions: { ref_name: { include: ["~DEFAULT_BRANCH"], exclude: [] } },
      bypass_actors: [
        { actor_id: $admin, actor_type: "RepositoryRole", bypass_mode: "pull_request" }
      ],
      rules: (
        [ { type: "deletion" }, { type: "non_fast_forward" } ]
        + (if $linear then [ { type: "required_linear_history" } ] else [] end)
        + [
          { type: "pull_request",
            parameters: {
              required_approving_review_count: 0,
              dismiss_stale_reviews_on_push: false,
              require_code_owner_review: false,
              require_last_push_approval: false,
              required_review_thread_resolution: false,
              allowed_merge_methods: ["squash"]
            } },
          { type: "required_status_checks",
            parameters: {
              strict_required_status_checks_policy: false,
              do_not_enforce_on_create: false,
              required_status_checks: ($checks | split(",") | map({ context: ., integration_id: $app }))
            } }
        ]
      )
    }'
}

while IFS='|' read -r repo checks linear; do
  [ -z "$repo" ] && continue
  if [ -n "$ONLY" ] && ! grep -qx "$repo" <<<"${ONLY//,/$'\n'}"; then continue; fi
  echo "=== $ORG/$repo ==="
  body=$(build_ruleset "$repo" "$checks" "$linear")
  if [ "$APPLY" = 0 ]; then
    echo "$body" | jq .
    continue
  fi
  existing=$(gh api "/repos/$ORG/$repo/rulesets" --jq ".[] | select(.name == \"$RULESET_NAME\") | .id")
  if [ -n "$existing" ]; then
    echo "updating ruleset $existing"
    echo "$body" | gh api -X PUT "/repos/$ORG/$repo/rulesets/$existing" --input - --jq '"ok: \(.name) [\(.enforcement)] id=\(.id)"'
  else
    echo "creating ruleset"
    echo "$body" | gh api -X POST "/repos/$ORG/$repo/rulesets" --input - --jq '"ok: \(.name) [\(.enforcement)] id=\(.id)"'
  fi
  gh api -X PATCH "/repos/$ORG/$repo" -F allow_squash_merge=true -F allow_merge_commit=false \
    -F allow_rebase_merge=false --jq '"merge settings: squash=\(.allow_squash_merge) merge=\(.allow_merge_commit) rebase=\(.allow_rebase_merge)"'
  db=$(gh api "/repos/$ORG/$repo" --jq .default_branch)
  if gh api "/repos/$ORG/$repo/branches/$db/protection" >/dev/null 2>&1; then
    echo "deleting classic branch protection on $db"
    gh api -X DELETE "/repos/$ORG/$repo/branches/$db/protection"
  fi
done <<<"$CONFIG"
