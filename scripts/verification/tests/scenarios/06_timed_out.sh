#!/usr/bin/env bash
# Auto commit's command takes longer than TIMEOUT_SECONDS. The
# verification should report failure_kind: timed_out with timeout_seconds.
# We override the timeout to 1s via SCENARIO_TIMEOUT.
set -euo pipefail
source "$(dirname "$0")/../lib.sh"
SCENARIO_TIMEOUT=1
setup_repo
make_commit "feat: substantive change" "a.txt=hello"
make_auto_commit "sleep 30 && echo done > c.txt" "c.txt=done
"
run_and_compare
