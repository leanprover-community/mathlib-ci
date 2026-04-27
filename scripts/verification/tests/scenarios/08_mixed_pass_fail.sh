#!/usr/bin/env bash
# Multiple failure kinds in one PR. The comment should render each
# failed commit with its own diagnosis, and the verified commits as
# one-liners.
set -euo pipefail
source "$(dirname "$0")/../lib.sh"
setup_repo
make_commit "feat: substantive change" "a.txt=hello"
make_auto_commit "echo one > b.txt" "b.txt=one
"
make_auto_commit "missing_command_xyzzy" "c.txt=anything"
make_auto_commit "echo three > d.txt" "d.txt=mismatch
"
make_auto_commit "echo four > e.txt" "e.txt=four
"
run_and_compare
