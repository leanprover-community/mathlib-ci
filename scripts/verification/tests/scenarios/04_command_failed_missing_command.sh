#!/usr/bin/env bash
# Auto commit's subject references a command that doesn't exist. bash
# prints "command not found"; verification reports failure_kind:
# command_failed with exit_code 127.
set -euo pipefail
source "$(dirname "$0")/../lib.sh"
setup_repo
make_commit "feat: substantive change" "a.txt=hello"
make_auto_commit "this_command_does_not_exist_xyzzy --flag" "c.txt=whatever"
run_and_compare
