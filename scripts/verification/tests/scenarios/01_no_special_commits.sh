#!/usr/bin/env bash
# Sanity case: a PR with one substantive commit and no auto/transient
# commits. Verification should succeed and the comment should list the
# substantive commit only.
set -euo pipefail
source "$(dirname "$0")/../lib.sh"
setup_repo
make_commit "feat: add a thing" "a.txt=hello"
run_and_compare
