#!/usr/bin/env bash
# Auto commit's subject is not valid bash (unbalanced quote). The
# replay should fail with a bash parse error captured verbatim in the
# JSON and rendered into a <details> block.
set -euo pipefail
source "$(dirname "$0")/../lib.sh"
setup_repo
make_commit "feat: substantive change" "a.txt=hello"
# Subject contains an unbalanced double quote — bash parse error.
make_auto_commit 'echo "broken' "c.txt=whatever"
run_and_compare
