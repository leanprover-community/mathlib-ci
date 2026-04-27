#!/usr/bin/env bash
# A non-transient commit interleaved with transient commits where
# the cherry-pick of the non-transient commit onto the base
# conflicts (because the transient commit set up state the
# non-transient one depends on). The script should detect this and
# report transient_failure_kind: cherry_pick_conflict with the
# offending commit's SHA and the cherry-pick output excerpt.
set -euo pipefail
source "$(dirname "$0")/../lib.sh"
setup_repo
# Base has no a.txt.
# Transient commit creates a.txt with content "X".
make_transient_commit "create a.txt" "a.txt=X
"
# Non-transient commit modifies a.txt — assumes "X" exists. Replaying
# this on top of the base (without the transient commit) fails.
make_commit "feat: append to a.txt" "a.txt=X then Y
"
run_and_compare
