#!/usr/bin/env bash
# A transient commit that actually has a net effect. Replaying the
# non-transient commits onto the base produces a tree that differs
# from HEAD's tree. The script should detect this and report
# transient_failure_kind: tree_mismatch with a diff_excerpt.
set -euo pipefail
source "$(dirname "$0")/../lib.sh"
setup_repo
make_commit "feat: substantive change" "a.txt=hello"
# Transient commit adds tmp.txt and is never removed — net effect is
# non-empty.
make_transient_commit "add scratch file" "tmp.txt=scratch
"
run_and_compare
