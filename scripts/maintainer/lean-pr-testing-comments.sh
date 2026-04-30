## Create comments and labels on a Lean 4 or Batteries PR after CI has finished on a `*-pr-testing-NNNN` branch.
##
## See https://leanprover-community.github.io/contribute/tags_and_branches.html

# Make this script robust against unintentional errors.
# See e.g. http://redsymbol.net/articles/unofficial-bash-strict-mode/ for explanation.
set -euo pipefail
IFS=$'\n\t'

# Ensure the first argument is either 'lean' or 'batteries'.
if [ -z "$1" ]; then
  echo "The first argument must be either 'lean' or 'batteries'"
  exit 1
fi

# Set NIGHTLY_TESTING_REPO for comparison URLs (where the branches and tags actually live)
if [ -z "${NIGHTLY_TESTING_REPO:-}" ]; then
  NIGHTLY_TESTING_REPO="leanprover-community/mathlib4-nightly-testing"
fi

# TODO: The whole script ought to be rewritten in javascript, to avoid having to use curl for API calls.
#
# This is not meant to be run from the command line, only from CI.
# The inputs must be prepared as:
# env:
#   TOKEN: ${{ secrets.LEAN_PR_TESTING }}
#   GITHUB_CONTEXT: ${{ toJson(github) }}
#   WORKFLOW_URL: https://github.com/${{ github.repository }}/actions/runs/${{ github.event.workflow_run.id }}
#   BUILD_OUTCOME: ${{ steps.build.outcome }}
#   NOISY_OUTCOME: ${{ steps.noisy.outcome }}
#   ARCHIVE_OUTCOME: ${{ steps.archive.outcome }}
#   COUNTEREXAMPLES_OUTCOME: ${{ steps.counterexamples.outcome }}
#   LINT_OUTCOME: ${{ steps.lint.outcome }}
#   TEST_OUTCOME: ${{ steps.test.outcome }}

# Adjust the branch pattern and URLs based on the repository.
if [ "$1" == "lean" ]; then
  branch_prefix="lean-pr-testing"
  repo_url="https://api.github.com/repos/leanprover/lean4"
  base_branch="nightly-testing" # This really should be the relevant `nightly-testing-YYYY-MM-DD` tag.
elif [ "$1" == "batteries" ]; then
  branch_prefix="batteries-pr-testing"
  repo_url="https://api.github.com/repos/leanprover-community/batteries"
  base_branch="master"
else
  echo "Unknown repository: $1. Must be either 'lean' or 'batteries'."
  exit 1
fi

# Extract branch name and check if it matches the pattern.
branch_name=$(echo "$GITHUB_CONTEXT" | jq -r .ref | cut -d'/' -f3)
if [[ "$branch_name" =~ ^$branch_prefix-([0-9]+)$ ]]; then
  pr_number="${BASH_REMATCH[1]}"
  current_time=$(date "+%Y-%m-%d %H:%M:%S")

  echo "This is a '$branch_prefix-$pr_number' branch, so we need to adjust labels and write a comment."

  # Check if the PR has an awaiting-mathlib label
  has_awaiting_label=$(curl -L -s \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    $repo_url/issues/$pr_number/labels | jq 'map(.name) | contains(["awaiting-mathlib"])')

  # Classify the run into one aggregate state, so labels and the status
  # comment cannot disagree. `failure` beats `cancelled` so a mixed run is
  # reported as breaking; everything that is neither all-success nor a failure
  # (cancelled, skipped, empty) clears both labels rather than leaving a stale
  # one from an earlier run.
  outcomes=("$BUILD_OUTCOME" "$NOISY_OUTCOME" "$ARCHIVE_OUTCOME" "$COUNTEREXAMPLES_OUTCOME" "$LINT_OUTCOME" "$TEST_OUTCOME")
  any_failure=false
  any_cancelled=false
  all_success=true
  for outcome in "${outcomes[@]}"; do
    if [ "$outcome" == "failure" ]; then any_failure=true; fi
    if [ "$outcome" == "cancelled" ]; then any_cancelled=true; fi
    if [ "$outcome" != "success" ]; then all_success=false; fi
  done

  if $all_success; then
    state="success"
  elif $any_failure; then
    state="failure"
  elif $any_cancelled; then
    state="cancelled"
  else
    state="other"
  fi

  remove_label() {
    echo "Removing label $1"
    curl -L -s \
      -X DELETE \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer $TOKEN" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "$repo_url/issues/$pr_number/labels/$1"
  }
  add_label() {
    echo "Adding label $1"
    curl -L -s \
      -X POST \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer $TOKEN" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "$repo_url/issues/$pr_number/labels" \
      -d "$(jq --null-input --arg name "$1" '{labels: [$name]}')"
  }

  case "$state" in
    success)
      remove_label awaiting-mathlib
      remove_label breaks-mathlib
      add_label builds-mathlib
      ;;
    failure)
      remove_label builds-mathlib
      add_label breaks-mathlib
      ;;
    cancelled|other)
      remove_label builds-mathlib
      remove_label breaks-mathlib
      ;;
  esac

  branch="[$branch_prefix-$pr_number](https://github.com/$NIGHTLY_TESTING_REPO/compare/$base_branch...$branch_prefix-$pr_number)"
  case "$state" in
    success)
      message="- ✅ Mathlib branch $branch has successfully built against this PR. ($current_time) [View Log]($WORKFLOW_URL)"
      ;;
    failure)
      # Pick the most informative failure message. Order matches the
      # historical precedence: build > lint > counterexamples > archive >
      # noisy > test.
      if [ "$BUILD_OUTCOME" == "failure" ]; then
        message="- 💥 Mathlib branch $branch build failed against this PR. ($current_time) [View Log]($WORKFLOW_URL)"
      elif [ "$LINT_OUTCOME" == "failure" ]; then
        message="- ❌ Mathlib branch $branch built against this PR, but linting failed. ($current_time) [View Log]($WORKFLOW_URL)"
      elif [ "$COUNTEREXAMPLES_OUTCOME" == "failure" ]; then
        message="- ❌ Mathlib branch $branch built against this PR, but the counterexamples library failed. ($current_time) [View Log]($WORKFLOW_URL)"
      elif [ "$ARCHIVE_OUTCOME" == "failure" ]; then
        message="- ❌ Mathlib branch $branch built against this PR, but the archive failed. ($current_time) [View Log]($WORKFLOW_URL)"
      elif [ "$NOISY_OUTCOME" == "failure" ]; then
        message="- ❌ Mathlib branch $branch built against this PR, but was unexpectedly noisy. ($current_time) [View Log]($WORKFLOW_URL)"
      else
        message="- ❌ Mathlib branch $branch built against this PR, but testing failed. ($current_time) [View Log]($WORKFLOW_URL)"
      fi
      ;;
    cancelled)
      message="- 🟡 Mathlib branch $branch build against this PR was cancelled. ($current_time) [View Log]($WORKFLOW_URL)"
      ;;
    other)
      message="- 🟡 Mathlib branch $branch build this PR didn't complete normally. ($current_time) [View Log]($WORKFLOW_URL)"
      ;;
  esac

  echo "$message"

  # Check if we should post a new comment or append to the existing one
  if [ "$has_awaiting_label" == "true" ]; then
    # Always post as a new comment if awaiting-mathlib label is present
    intro="Mathlib CI status ([docs](https://leanprover-community.github.io/contribute/tags_and_branches.html)):"
    echo "Posting as a separate comment due to awaiting-mathlib label at $repo_url/issues/$pr_number/comments"
    curl -L -s \
      -X POST \
      -H "Authorization: token $TOKEN" \
      -H "Accept: application/vnd.github.v3+json" \
      -d "$(jq --null-input --arg val "$message" '{"body": $val}')" \
      "$repo_url/issues/$pr_number/comments"
  else
    # Use existing behavior: append to existing comment or post a new one
    # Use GitHub API to check if a comment already exists
    existing_comment=$(curl -L -s -H "Authorization: token $TOKEN" \
                            -H "Accept: application/vnd.github.v3+json" \
                            "$repo_url/issues/$pr_number/comments" \
                            | jq 'first(.[] | select(.body | test("^- . Mathlib") or startswith("Mathlib CI status")) | select(.user.login == "mathlib-lean-pr-testing[bot]"))')
    existing_comment_id=$(echo "$existing_comment" | jq -r .id)
    existing_comment_body=$(echo "$existing_comment" | jq -r .body)

    # Append new result to the existing comment or post a new comment
    if [ -z "$existing_comment_id" ]; then
      # Post new comment with a bullet point
      intro="Mathlib CI status ([docs](https://leanprover-community.github.io/contribute/tags_and_branches.html)):"
      echo "Posting as new comment at $repo_url/issues/$pr_number/comments"
      curl -L -s \
        -X POST \
        -H "Authorization: token $TOKEN" \
        -H "Accept: application/vnd.github.v3+json" \
        -d "$(jq --null-input --arg intro "$intro" --arg val "$message" '{"body": ($intro + "\n" + $val)}')" \
        "$repo_url/issues/$pr_number/comments"
    else
      # Append new result to the existing comment
      echo "Appending to existing comment at $repo_url/issues/$pr_number/comments"
      curl -L -s \
        -X PATCH \
        -H "Authorization: token $TOKEN" \
        -H "Accept: application/vnd.github.v3+json" \
        -d "$(jq --null-input --arg existing "$existing_comment_body" --arg message "$message" '{"body":($existing + "\n" + $message)}')" \
        "$repo_url/issues/comments/$existing_comment_id"
    fi
  fi
fi
