#!/usr/bin/env bash
# All auto commits succeed. The comment shows them as a list of
# checkmarks; no <details> blocks.
set -euo pipefail
source "$(dirname "$0")/../lib.sh"
setup_repo
make_commit "feat: substantive change" "a.txt=hello"
make_auto_commit "echo OK > b.txt" "b.txt=OK
"
make_auto_commit "echo OK2 > c.txt" "c.txt=OK2
"
run_and_compare
