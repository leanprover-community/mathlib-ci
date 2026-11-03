## Report Mathlib CI results on the Batteries PR named by `batteries-pr-testing-NNNN`.
##
## See https://leanprover-community.github.io/contribute/tags_and_branches.html

# Make this script robust against unintentional errors.
# See e.g. http://redsymbol.net/articles/unofficial-bash-strict-mode/ for explanation.
set -euo pipefail
IFS=$'\n\t'

# Ensure the first argument is 'batteries'.
# This argument (as well as the script name) is left over from when this script
# used to handle lean PR testing as well.
if [ "${1:-}" != "batteries" ]; then
  echo "The first argument must be 'batteries'"
  exit 1
fi

# Set NIGHTLY_TESTING_REPO for comparison URLs (where the branches actually live)
if [ -z "${NIGHTLY_TESTING_REPO:-}" ]; then
  NIGHTLY_TESTING_REPO="leanprover-community/mathlib4-nightly-testing"
fi

# This is not meant to be run from the command line, only from CI.
# The inputs must be prepared as:
# env:
#   TOKEN: ${{ secrets.batteries-pr-testing-token.outputs.token }}
#   BOT_LOGIN: Login of the App that supplies TOKEN.
#   GITHUB_CONTEXT: ${{ toJson(github) }}
#   WORKFLOW_URL: https://github.com/${{ github.repository }}/actions/runs/${{ github.run_id }}
#   BATTERIES_SHA: Optional tested Batteries revision; skips reports for an outdated pin.
#   BUILD_OUTCOME: ${{ steps.build.outcome }}
#   NOISY_OUTCOME: ${{ steps.noisy.outcome }}
#   ARCHIVE_OUTCOME: ${{ steps.archive.outcome }}
#   COUNTEREXAMPLES_OUTCOME: ${{ steps.counterexamples.outcome }}
#   LINT_OUTCOME: ${{ steps.lint.outcome }}
#   TEST_OUTCOME: ${{ steps.test.outcome }}
#   WANTED_OUTCOME, MK_ALL_OUTCOME: Optional additional build checks.

BOT_LOGIN="${BOT_LOGIN:-mathlib-nightly-testing[bot]}"

branch_prefix="batteries-pr-testing"
repo_url="https://api.github.com/repos/leanprover-community/batteries"
base_branch="master"

# Extract branch name and check if it matches the pattern.
branch_name=$(echo "$GITHUB_CONTEXT" | jq -r '.event.pull_request.head.ref // (.ref | sub("^refs/heads/"; ""))')
if [[ "$branch_name" =~ ^$branch_prefix-([0-9]+)$ ]]; then
  pr_number="${BASH_REMATCH[1]}"
  current_time=$(date "+%Y-%m-%d %H:%M:%S")

  echo "This is a '$branch_prefix-$pr_number' branch, so we need to adjust labels and write a comment."

  source_pr=$(curl -L -sS --fail-with-body \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $TOKEN" \
    "$repo_url/pulls/$pr_number")
  # A newer Batteries commit can arrive before its adaptation branch updates.
  if [ -n "${BATTERIES_SHA:-}" ]; then
    if jq -e --arg sha "$BATTERIES_SHA" '.state == "open" and .head.sha != $sha' <<<"$source_pr" > /dev/null; then
      echo "The Batteries pin is outdated; skipping this report."
      exit 0
    fi
  fi

  has_awaiting_label=$(jq '.labels | map(.name) | contains(["awaiting-mathlib"])' <<<"$source_pr")

  # Classify the run into one aggregate state, so labels and the status
  # comment cannot disagree. `failure` beats `cancelled` so a mixed run is
  # reported as breaking; everything that is neither all-success nor a failure
  # (cancelled, skipped, empty) clears both labels rather than leaving a stale
  # one from an earlier run.
  outcomes=("$BUILD_OUTCOME" "$NOISY_OUTCOME" "$ARCHIVE_OUTCOME" "$COUNTEREXAMPLES_OUTCOME" "$LINT_OUTCOME" "$TEST_OUTCOME"
            "${WANTED_OUTCOME-success}" "${MK_ALL_OUTCOME-success}")
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
    # GitHub returns 404 when the label isn't currently on the PR; that's the
    # desired post-condition, so accept it. curl exits 22 on any 4xx with
    # --fail-with-body, so we have to inspect the status code ourselves.
    local http_code
    http_code=$(curl -L -sS \
      -o /dev/null -w "%{http_code}" \
      -X DELETE \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer $TOKEN" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "$repo_url/issues/$pr_number/labels/$1")
    case "$http_code" in
      200|404) ;;
      *) echo "Failed to remove label $1: HTTP $http_code" >&2; return 1 ;;
    esac
  }
  add_label() {
    echo "Adding label $1"
    curl -L -sS --fail-with-body \
      -X POST \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer $TOKEN" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "$repo_url/issues/$pr_number/labels" \
      -d "$(jq --null-input --arg name "$1" '{labels: [$name]}')"
  }

  adaptation_url=$(echo "$GITHUB_CONTEXT" | jq -r '.event.pull_request.html_url // empty')
  branch="[$branch_prefix-$pr_number](${adaptation_url:-https://github.com/$NIGHTLY_TESTING_REPO/compare/$base_branch...$branch_prefix-$pr_number})"
  case "$state" in
    success)
      message="- ✅ Mathlib branch $branch has successfully built against this PR. ($current_time) [View Log]($WORKFLOW_URL)"
      ;;
    failure)
      # Pick the most informative failure message. Order matches the
      # precedence: build > lint > counterexamples > archive > Wanted > imports > noisy > test.
      if [ "$BUILD_OUTCOME" == "failure" ]; then
        message="- 💥 Mathlib branch $branch build failed against this PR. ($current_time) [View Log]($WORKFLOW_URL)"
      elif [ "$LINT_OUTCOME" == "failure" ]; then
        message="- ❌ Mathlib branch $branch built against this PR, but linting failed. ($current_time) [View Log]($WORKFLOW_URL)"
      elif [ "$COUNTEREXAMPLES_OUTCOME" == "failure" ]; then
        message="- ❌ Mathlib branch $branch built against this PR, but the counterexamples library failed. ($current_time) [View Log]($WORKFLOW_URL)"
      elif [ "$ARCHIVE_OUTCOME" == "failure" ]; then
        message="- ❌ Mathlib branch $branch built against this PR, but the archive failed. ($current_time) [View Log]($WORKFLOW_URL)"
      elif [ "${WANTED_OUTCOME:-}" == "failure" ]; then
        message="- ❌ Mathlib branch $branch built against this PR, but the Wanted library failed. ($current_time) [View Log]($WORKFLOW_URL)"
      elif [ "${MK_ALL_OUTCOME:-}" == "failure" ]; then
        message="- ❌ Mathlib branch $branch built against this PR, but the import check failed. ($current_time) [View Log]($WORKFLOW_URL)"
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
    curl -L -sS --fail-with-body \
      -X POST \
      -H "Authorization: token $TOKEN" \
      -H "Accept: application/vnd.github.v3+json" \
      -d "$(jq --null-input --arg val "$message" '{"body": $val}')" \
      "$repo_url/issues/$pr_number/comments"
  else
    # Search all pages so long discussions still use one status comment.
    existing_comment=$(GH_TOKEN="$TOKEN" gh api --paginate --slurp "$repo_url/issues/$pr_number/comments" \
      | jq --arg bot "$BOT_LOGIN" 'first(.[][] | select(.body | test("^- . Mathlib") or startswith("Mathlib CI status")) | select(.user.login == $bot))')
    existing_comment_id=$(echo "$existing_comment" | jq -r .id)
    existing_comment_body=$(echo "$existing_comment" | jq -r .body)

    # Append new result to the existing comment or post a new comment
    if [ -z "$existing_comment_id" ]; then
      # Post new comment with a bullet point
      intro="Mathlib CI status ([docs](https://leanprover-community.github.io/contribute/tags_and_branches.html)):"
      echo "Posting as new comment at $repo_url/issues/$pr_number/comments"
      curl -L -sS --fail-with-body \
        -X POST \
        -H "Authorization: token $TOKEN" \
        -H "Accept: application/vnd.github.v3+json" \
        -d "$(jq --null-input --arg intro "$intro" --arg val "$message" '{"body": ($intro + "\n" + $val)}')" \
        "$repo_url/issues/$pr_number/comments"
    else
      # Append new result to the existing comment
      echo "Appending to existing comment at $repo_url/issues/$pr_number/comments"
      curl -L -sS --fail-with-body \
        -X PATCH \
        -H "Authorization: token $TOKEN" \
        -H "Accept: application/vnd.github.v3+json" \
        -d "$(jq --null-input --arg existing "$existing_comment_body" --arg message "$message" '{"body":($existing + "\n" + $message)}')" \
        "$repo_url/issues/comments/$existing_comment_id"
    fi
  fi

  # Clear awaiting-mathlib only after its notification succeeds.
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
fi
