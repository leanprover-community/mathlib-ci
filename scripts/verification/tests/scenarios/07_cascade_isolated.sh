#!/usr/bin/env bash
# Regression test for the leanprover-community/mathlib4#38480 failure
# pattern: an earlier auto commit fails AND its command would have
# created an untracked file. Without worktree isolation, that file
# leaks into the next commit's parent checkout, causing a cascade of
# spurious "checkout would overwrite" errors.
#
# With worktree isolation, each commit is verified independently:
#   - commit A fails (parse error) — no committed effects
#   - commit B succeeds (creates "stray.txt", which IS in B's tree)
#   - commit C succeeds (deletes "stray.txt"; C's tree has no stray.txt)
# All three verifications run against their own parents in fresh
# worktrees, so B's effects don't poison A's or C's replay.
set -euo pipefail
source "$(dirname "$0")/../lib.sh"
setup_repo
make_commit "feat: substantive change" "a.txt=hello"
# A: parse error in subject
make_auto_commit 'echo "unterminated > stray.txt' "c.txt=whatever"
# B: creates stray.txt (committed)
make_auto_commit "echo stray > stray.txt" "stray.txt=stray
"
# C: removes stray.txt (deletion committed)
make_auto_commit "rm stray.txt" "stray.txt=<DELETE>"
run_and_compare
