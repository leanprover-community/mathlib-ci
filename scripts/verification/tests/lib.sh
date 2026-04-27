#!/usr/bin/env bash
# Shared helpers for verify_commits.sh test scenarios.
#
# Each scenario file sources this and then calls (in order):
#   setup_repo
#   <some sequence of make_*_commit calls>
#   run_and_compare
#
# Determinism: scenarios use fixed author/committer identities and dates
# so commit SHAs are stable across runs. SHAs are also normalized out
# of the captured JSON / rendered comment before comparing against
# golden files, so the goldens stay readable and survive minor git
# version differences.

set -euo pipefail

# Script paths (resolved relative to this lib.sh)
TESTS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VERIFY_SCRIPT="$TESTS_DIR/../verify_commits.sh"
SUMMARY_SCRIPT="$TESTS_DIR/../verify_commits_summary.sh"
GOLDEN_DIR="$TESTS_DIR/golden"

# Fake repo identity for deterministic commits
export GIT_AUTHOR_NAME="Test"
export GIT_AUTHOR_EMAIL="test@example"
export GIT_COMMITTER_NAME="Test"
export GIT_COMMITTER_EMAIL="test@example"

# Fake repo coordinates for the rendered comment
TEST_REPO="example/repo"
TEST_PR_NUMBER="1"

# Per-commit timestamp counter — bumped by each make_*_commit so commits
# don't share a timestamp (which can cause SHA collisions in some edge
# cases).
_TS_COUNTER=0
_next_ts() {
  _TS_COUNTER=$((_TS_COUNTER + 1))
  printf '2024-01-01T00:00:%02dZ' "$_TS_COUNTER"
}

# Create a fresh empty git repo in a tmp dir, cd into it, return the path.
setup_repo() {
  REPO_DIR=$(mktemp -d -t verify_commits_test.XXXXXX)
  cd "$REPO_DIR"
  git init -q -b master
  # Initial empty commit so we have a base ref to compare against.
  GIT_AUTHOR_DATE="$(_next_ts)" GIT_COMMITTER_DATE="$(_next_ts)" \
    git commit -q --allow-empty -m "init"
  git checkout -q -b feature
}

# make_commit <subject> [<file>=<content>...]
# Stage the given file=content pairs (or none) and commit with the given
# subject using the next deterministic timestamp.
make_commit() {
  local subject="$1"; shift
  for spec in "$@"; do
    local file="${spec%%=*}"
    local content="${spec#*=}"
    if [[ "$content" == "<DELETE>" ]]; then
      rm -f "$file"
    else
      printf '%s' "$content" > "$file"
    fi
    git add "$file"
  done
  GIT_AUTHOR_DATE="$(_next_ts)" GIT_COMMITTER_DATE="$(_next_ts)" \
    git commit -q --allow-empty -m "$subject"
}

# make_auto_commit <command> [<file>=<content>...]
# Make an auto commit with subject "x: <command>" containing the given
# file changes. The actual file contents committed are exactly what's
# specified — independent of what running <command> would produce.
# This lets us craft "tree mismatch" cases where the committed tree
# doesn't match the command's output, "missing command" cases where
# the command can't even run, etc.
make_auto_commit() {
  local command="$1"; shift
  make_commit "x: $command" "$@"
}

# make_passing_auto_commit <command> <file>=<expected_content_after_running_command>
# Convenience for creating an auto commit where running the command
# would actually produce the committed file. Stores the file with the
# expected content; the verification script's replay should match.
make_passing_auto_commit() {
  make_auto_commit "$@"
}

# make_transient_commit <subject_after_prefix> [<file>=<content>...]
make_transient_commit() {
  local subject="$1"; shift
  make_commit "transient: $subject" "$@"
}

# Run verify_commits.sh against the current repo, capturing JSON,
# stderr, and exit code.
run_verify() {
  RAW_JSON_FILE=$(mktemp)
  STDERR_FILE=$(mktemp)
  set +e
  # Use a low timeout so timeout-test scenarios don't take 10 minutes.
  TIMEOUT_SECONDS="${SCENARIO_TIMEOUT:-5}" \
    "$VERIFY_SCRIPT" master --json-file "$RAW_JSON_FILE" >/dev/null 2>"$STDERR_FILE"
  VERIFY_EXIT=$?
  set -e
}

# Run the summary script against the captured JSON.
run_summary() {
  COMMENT_FILE=$(mktemp)
  "$SUMMARY_SCRIPT" "$TEST_REPO" "$TEST_PR_NUMBER" < "$RAW_JSON_FILE" > "$COMMENT_FILE"
}

# Strip nondeterministic fields from JSON / comment so goldens are
# stable across git versions and platforms. Replaces full SHAs with
# <SHA> and short (7-char) SHAs in code spans with <SHORT>.
normalize_output() {
  local file="$1"
  # Replace any 7+ hex-char run with <SHA>. This catches both full 40-char
  # SHAs and 7-char abbreviations regardless of surrounding markup.
  sed -E -i.bak \
    -e 's/[0-9a-f]{7,40}/<SHA>/g' \
    "$file"
  rm -f "$file.bak"
}

# Compare $1 against golden $2; on mismatch either fail or update.
compare_or_update() {
  local actual="$1" golden="$2"
  if [[ "${UPDATE_GOLDEN:-}" == "1" ]]; then
    mkdir -p "$(dirname "$golden")"
    cp "$actual" "$golden"
    echo "  updated: $(basename "$golden")"
    return 0
  fi
  if [[ ! -f "$golden" ]]; then
    echo "  MISSING: $golden" >&2
    echo "  hint: run with UPDATE_GOLDEN=1 to create it" >&2
    return 1
  fi
  if diff -u "$golden" "$actual" > /tmp/scenario_diff.txt 2>&1; then
    return 0
  fi
  echo "  DIFF for $(basename "$golden"):" >&2
  cat /tmp/scenario_diff.txt >&2
  return 1
}

# Run verify, run summary, normalize, and compare against goldens.
# Also captures the verify exit code into a separate golden for
# regression coverage.
run_and_compare() {
  local name="${SCENARIO_NAME:?SCENARIO_NAME must be set by the scenario}"
  run_verify
  run_summary

  # Pretty-print JSON before normalizing so goldens are readable.
  local pretty_json="$RAW_JSON_FILE.pretty"
  jq . "$RAW_JSON_FILE" > "$pretty_json"

  normalize_output "$pretty_json"
  normalize_output "$COMMENT_FILE"

  # Capture exit code to its own file
  local exit_file
  exit_file=$(mktemp)
  echo "$VERIFY_EXIT" > "$exit_file"

  local ok=true
  compare_or_update "$pretty_json" "$GOLDEN_DIR/$name.json" || ok=false
  compare_or_update "$COMMENT_FILE" "$GOLDEN_DIR/$name.md"  || ok=false
  compare_or_update "$exit_file"    "$GOLDEN_DIR/$name.exit" || ok=false

  if [[ "$ok" != "true" ]]; then
    return 1
  fi
  return 0
}

# Helper called by run.sh; prints scenario stderr only on failure.
# Returns 0/1; exit code is propagated.
run_scenario_file() {
  local f="$1"
  local name
  name=$(basename "$f" .sh)
  printf '%-50s' "  $name ... "
  local log
  log=$(mktemp)
  set +e
  ( SCENARIO_NAME="$name" bash "$f" ) > "$log" 2>&1
  local rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    echo "ok"
  else
    echo "FAIL"
    sed 's/^/    /' "$log" >&2
  fi
  rm -f "$log"
  return $rc
}
