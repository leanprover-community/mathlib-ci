#!/usr/bin/env bash
# Auto commit's command runs successfully but produces a different tree
# than what's actually committed. The verification should detect this
# and report failure_kind: tree_mismatch with a diff_excerpt.
set -euo pipefail
source "$(dirname "$0")/../lib.sh"
setup_repo
make_commit "feat: substantive change" "a.txt=hello"
# Commits a file with content "actual", but the subject claims the
# command writes "expected". Replay produces "expected"; tree mismatch.
make_auto_commit "echo expected > c.txt" "c.txt=actual
"
run_and_compare
