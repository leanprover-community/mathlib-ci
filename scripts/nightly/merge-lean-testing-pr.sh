#!/usr/bin/env bash
set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <PR number>"
    exit 1
fi

PR_NUMBER=$1
BRANCH_NAME="lean-pr-testing-$PR_NUMBER"
NIGHTLY_URL="https://github.com/leanprover-community/mathlib4-nightly-testing.git"

# Find the remote that hosts a given GitHub repository.
# Returns the remote name, or empty string if not found.
find_remote() {
  local repo_pattern="$1"
  git remote -v | grep -E "$repo_pattern(\.git)? \(fetch\)" | head -n1 | cut -f1 || true
}

# Detect the remote hosting mathlib4-nightly-testing, if any.
NIGHTLY_REMOTE=$(find_remote "leanprover-community/mathlib4-nightly-testing")

# Ensure a usable nightly-testing remote exists and points at the expected repo.
ensure_nightly_remote() {
    if [ -n "$NIGHTLY_REMOTE" ]; then
        return
    fi

    if git remote get-url nightly-testing >/dev/null 2>&1; then
        git remote set-url nightly-testing "$NIGHTLY_URL"
    else
        git remote add nightly-testing "$NIGHTLY_URL"
    fi

    NIGHTLY_REMOTE=nightly-testing
}

# Helper: ensure a local nightly-testing branch exists and is up-to-date.
# Fetches the branch, then either fast-forwards or creates it.
# Also ensures the local branch tracks <remote>/nightly-testing so a later plain
# `git push` goes to mathlib4-nightly-testing rather than the clone's default remote.
checkout_nightly() {
    local remote_name="$1"
    git fetch "$remote_name" nightly-testing
    if git rev-parse --verify refs/heads/nightly-testing >/dev/null 2>&1; then
        git checkout nightly-testing
        git branch --set-upstream-to="$remote_name/nightly-testing" nightly-testing
        git merge --ff-only FETCH_HEAD
    else
        git checkout --track -b nightly-testing "$remote_name/nightly-testing"
    fi
}

ensure_nightly_remote
checkout_nightly "$NIGHTLY_REMOTE"
git fetch "$NIGHTLY_REMOTE" "$BRANCH_NAME"
MERGE_REF="FETCH_HEAD"

if ! git merge "$MERGE_REF"; then
    echo "Merge conflicts detected. Resolving conflicts in favor of current version..."
    git checkout --ours lean-toolchain lakefile.lean lake-manifest.json
    git add lean-toolchain lakefile.lean lake-manifest.json
fi

sed "s/$BRANCH_NAME/nightly-testing/g" < lakefile.lean > lakefile.lean.new
mv lakefile.lean.new lakefile.lean
git add lakefile.lean

# Check for merge conflicts
if git ls-files -u | grep -q '^'; then
    echo "Merge conflicts detected. Please resolve conflicts manually."
    git status
    exit 1
fi

if ! lake update -v; then
    echo "Lake update failed. Please resolve conflicts manually."
    git status
    exit 1
fi

# Add files touched by lake update
git add lakefile.lean lake-manifest.json

# Attempt to commit. This will fail if there are conflicts.
if git commit -m "merge $BRANCH_NAME"; then
    echo "Merge successful."
    # Note: This script does NOT push. The caller is responsible for pushing.
    # This allows the nightly_bump_and_merge.yml workflow to batch multiple
    # merges into a single push, avoiding spurious CI failures.
    exit 0
else
    echo "Merge failed. Please resolve conflicts manually."
    git status
    exit 1
fi
