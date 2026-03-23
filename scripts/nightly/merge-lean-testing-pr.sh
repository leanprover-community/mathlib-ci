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

# Helper: ensure a local nightly-testing branch exists and is up-to-date.
# Fetches the branch, then either fast-forwards or creates it.
checkout_nightly() {
    local fetch_source="$1"
    git fetch "$fetch_source" nightly-testing
    if git rev-parse --verify refs/heads/nightly-testing >/dev/null 2>&1; then
        git checkout nightly-testing
        git merge --ff-only FETCH_HEAD
    else
        git checkout -b nightly-testing FETCH_HEAD
    fi
}

if [ -n "$NIGHTLY_REMOTE" ]; then
    checkout_nightly "$NIGHTLY_REMOTE"
    MERGE_REF="$NIGHTLY_REMOTE/$BRANCH_NAME"
else
    # No configured remote for nightly-testing; fetch by URL without adding a remote.
    checkout_nightly "$NIGHTLY_URL"
    git fetch "$NIGHTLY_URL" "$BRANCH_NAME"
    MERGE_REF="FETCH_HEAD"
fi

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
