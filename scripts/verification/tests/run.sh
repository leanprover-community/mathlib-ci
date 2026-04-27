#!/usr/bin/env bash
# Test driver for verify_commits.sh.
#
# Usage:
#   ./run.sh                # run all scenarios
#   ./run.sh 03 05          # run scenarios matching these names
#   UPDATE_GOLDEN=1 ./run.sh    # write goldens instead of comparing
#
# Exit code: 0 if all scenarios pass, 1 otherwise.

set -euo pipefail

TESTS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$TESTS_DIR/lib.sh"

SCENARIO_FILTER=("$@")

shopt -s nullglob
SCENARIO_FILES=("$TESTS_DIR"/scenarios/*.sh)

if [[ ${#SCENARIO_FILES[@]} -eq 0 ]]; then
  echo "no scenarios found in $TESTS_DIR/scenarios/" >&2
  exit 1
fi

filter_match() {
  local name="$1"
  if [[ ${#SCENARIO_FILTER[@]} -eq 0 ]]; then
    return 0
  fi
  for needle in "${SCENARIO_FILTER[@]}"; do
    [[ "$name" == *"$needle"* ]] && return 0
  done
  return 1
}

echo "running verify_commits scenarios..."
PASS=0; FAIL=0; SKIP=0
for f in "${SCENARIO_FILES[@]}"; do
  name=$(basename "$f" .sh)
  if ! filter_match "$name"; then
    SKIP=$((SKIP + 1))
    continue
  fi
  if run_scenario_file "$f"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
  fi
done

echo ""
echo "  passed: $PASS"
echo "  failed: $FAIL"
[[ $SKIP -gt 0 ]] && echo "  skipped: $SKIP"

[[ $FAIL -eq 0 ]]
