#!/usr/bin/env bash
# Commit Verification Script
# Verifies transient and automated commits in a PR.
#
# Usage: ./scripts/verification/verify_commits.sh <base_ref> [--json | --json-file <path>]
#
# Exit codes:
#   0 - All verifications passed
#   1 - Verification failed
#   2 - Usage error
#
# Each automated commit is replayed in an isolated `git worktree` checked out
# at the commit's parent. The replay is independent of the main checkout and
# of other commits' replays, so failures and stray output never bleed across
# commits or back into the user's working tree. Combined stdout+stderr is
# `tee`'d so it stays visible in CI logs while being captured for the JSON
# report. JSON output is built via `jq -nc --arg ...` so commit subjects with
# arbitrary characters (newlines, backslashes, control bytes) round-trip
# safely.

set -euo pipefail

# --- Configuration ---
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-600}"  # 10 minutes per command (env-overridable for tests)
TRANSIENT_PREFIX="${TRANSIENT_PREFIX:-transient: }"
# Support both "x <cmd>" and "x: <cmd>" (legacy) formats
AUTO_PREFIX_COLON="${AUTO_PREFIX_COLON:-x: }"
AUTO_PREFIX_SPACE="${AUTO_PREFIX_SPACE:-x }"

# Excerpt limits — capture at most this many bytes/lines of command output
# or diff stat for inclusion in the JSON report (and ultimately the comment).
# The summary script applies a separate overall comment-size cap.
MAX_EXCERPT_BYTES=4096
MAX_EXCERPT_LINES=40

# --- Colors (disabled if not a terminal) ---
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[0;33m'
  BLUE='\033[0;34m'
  NC='\033[0m'
else
  RED='' GREEN='' YELLOW='' BLUE='' NC=''
fi

log_info()  { echo -e "${BLUE}[INFO]${NC} $*" >&2; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $*" >&2; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

usage() {
  cat >&2 <<EOF
Usage: $0 <base_ref> [--json | --json-file <path>]

Arguments:
  base_ref          The base commit/branch to compare against (e.g., origin/master)
  --json            Output results in JSON format (instead of human-readable)
  --json-file PATH  Write JSON to PATH while outputting human-readable to stdout
EOF
  exit 2
}

# --- Argument parsing ---
JSON_OUTPUT=false
JSON_FILE=""
BASE_REF=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON_OUTPUT=true; shift ;;
    --json-file) JSON_FILE="$2"; shift 2 ;;
    --help|-h) usage ;;
    -*) log_error "Unknown option: $1"; usage ;;
    *) BASE_REF="$1"; shift ;;
  esac
done

if [[ -z "$BASE_REF" ]]; then
  log_error "Missing required argument: base_ref"
  usage
fi

# --- Subject / command helpers ---
is_auto_commit() {
  local subject="$1"
  [[ "$subject" == "$AUTO_PREFIX_COLON"* || "$subject" == "$AUTO_PREFIX_SPACE"* ]]
}

get_auto_command() {
  local subject="$1"
  if [[ "$subject" == "$AUTO_PREFIX_COLON"* ]]; then
    printf %s "${subject#$AUTO_PREFIX_COLON}"
  else
    printf %s "${subject#$AUTO_PREFIX_SPACE}"
  fi
}

# --- Excerpt extraction ---
# Extract the last N bytes/lines of a file. Sets EXCERPT and EXCERPT_TRUNCATED.
# Globals are used so the result can include arbitrary bytes (incl. newlines)
# without splitting issues.
EXCERPT=""
EXCERPT_TRUNCATED=false
extract_excerpt() {
  local file="$1"
  local total_bytes total_lines
  if [[ ! -s "$file" ]]; then
    EXCERPT=""
    EXCERPT_TRUNCATED=false
    return
  fi
  total_bytes=$(wc -c < "$file" | tr -d ' ')
  total_lines=$(wc -l < "$file" | tr -d ' ')
  EXCERPT=$(tail -c "$MAX_EXCERPT_BYTES" "$file" | tail -n "$MAX_EXCERPT_LINES")
  if [[ $total_bytes -gt $MAX_EXCERPT_BYTES ]] || [[ $total_lines -gt $MAX_EXCERPT_LINES ]]; then
    EXCERPT_TRUNCATED=true
  else
    EXCERPT_TRUNCATED=false
  fi
}

# Compute a truncated `git diff-tree --stat` between two trees.
# Sets DIFF_EXCERPT and DIFF_EXCERPT_TRUNCATED.
DIFF_EXCERPT=""
DIFF_EXCERPT_TRUNCATED=false
extract_diff_excerpt() {
  local tree1="$1" tree2="$2"
  local diff_full total_bytes total_lines
  diff_full=$(git diff-tree --stat "$tree1" "$tree2" 2>&1 || true)
  total_bytes=$(printf %s "$diff_full" | wc -c | tr -d ' ')
  total_lines=$(printf '%s\n' "$diff_full" | wc -l | tr -d ' ')
  DIFF_EXCERPT=$(printf %s "$diff_full" | tail -c "$MAX_EXCERPT_BYTES" | tail -n "$MAX_EXCERPT_LINES")
  if [[ $total_bytes -gt $MAX_EXCERPT_BYTES ]] || [[ $total_lines -gt $MAX_EXCERPT_LINES ]]; then
    DIFF_EXCERPT_TRUNCATED=true
  else
    DIFF_EXCERPT_TRUNCATED=false
  fi
}

# --- Worktree management ---
# All replay worktrees live under a single mktemp'd parent so cleanup is
# trivial and isolated from any other worktrees the caller may have.
WORKTREE_BASE=""
declare -a CREATED_WORKTREES=()
JSON_TMPDIR=""

init_tmpdirs() {
  [[ -n "$WORKTREE_BASE" ]] && return
  WORKTREE_BASE=$(mktemp -d -t verify_commits.XXXXXX)
  JSON_TMPDIR=$(mktemp -d -t verify_commits_json.XXXXXX)
}

create_worktree() {
  local target_ref="$1" label="$2"
  init_tmpdirs
  local wt_path="$WORKTREE_BASE/$label"
  git worktree add -q --detach "$wt_path" "$target_ref" >&2
  CREATED_WORKTREES+=("$wt_path")
  printf %s "$wt_path"
}

remove_worktree() {
  local wt_path="$1"
  git worktree remove --force "$wt_path" 2>/dev/null || rm -rf "$wt_path"
}

cleanup_all() {
  if [[ ${#CREATED_WORKTREES[@]} -gt 0 ]]; then
    for wt in "${CREATED_WORKTREES[@]}"; do
      git worktree remove --force "$wt" 2>/dev/null || true
    done
  fi
  [[ -n "$WORKTREE_BASE" ]] && rm -rf "$WORKTREE_BASE"
  [[ -n "$JSON_TMPDIR" ]]   && rm -rf "$JSON_TMPDIR"
  git worktree prune 2>/dev/null || true
}

trap cleanup_all EXIT

# Clear stale state from any previous interrupted run.
git worktree prune 2>/dev/null || true

# --- Main: collect and categorize commits ---
MERGE_BASE=$(git merge-base "$BASE_REF" HEAD)
log_info "Merge base: $MERGE_BASE"
log_info "HEAD: $(git rev-parse HEAD)"

ALL_COMMITS=()
while IFS= read -r _commit_line; do
  ALL_COMMITS+=("$_commit_line")
done < <(git rev-list --reverse "$MERGE_BASE..HEAD")
TOTAL_COMMITS=${#ALL_COMMITS[@]}
log_info "Found $TOTAL_COMMITS commits to analyze"

declare -a TRANSIENT_COMMITS=()
declare -a NON_TRANSIENT_COMMITS=()
declare -a AUTO_COMMITS=()
declare -a SUBSTANTIVE_COMMITS=()

for commit in "${ALL_COMMITS[@]:-}"; do
  [[ -z "$commit" ]] && continue
  subject=$(git log -1 --format="%s" "$commit")
  if [[ "$subject" == "$TRANSIENT_PREFIX"* ]]; then
    TRANSIENT_COMMITS+=("$commit")
  else
    NON_TRANSIENT_COMMITS+=("$commit")
    if is_auto_commit "$subject"; then
      AUTO_COMMITS+=("$commit")
    else
      SUBSTANTIVE_COMMITS+=("$commit")
    fi
  fi
done

log_info "Categorized: ${#SUBSTANTIVE_COMMITS[@]} substantive, ${#AUTO_COMMITS[@]} automated, ${#TRANSIENT_COMMITS[@]} transient"

init_tmpdirs
SUBSTANTIVE_NDJSON="$JSON_TMPDIR/substantive.ndjson"
AUTO_NDJSON="$JSON_TMPDIR/auto.ndjson"
TRANSIENT_NDJSON="$JSON_TMPDIR/transient.ndjson"
: > "$SUBSTANTIVE_NDJSON"
: > "$AUTO_NDJSON"
: > "$TRANSIENT_NDJSON"

# --- Verification state ---
TRANSIENT_VERIFIED=true
TRANSIENT_FAILURE_KIND=""
TRANSIENT_FAILED_SHA=""
TRANSIENT_FAILED_SUBJECT=""
TRANSIENT_OUTPUT_EXCERPT=""
TRANSIENT_OUTPUT_TRUNCATED=false
TRANSIENT_DIFF_EXCERPT=""
TRANSIENT_DIFF_TRUNCATED=false
OVERALL_SUCCESS=true

# --- Substantive commits: just record subject for the report ---
for commit in "${SUBSTANTIVE_COMMITS[@]:-}"; do
  [[ -z "$commit" ]] && continue
  subject=$(git log -1 --format="%s" "$commit")
  jq -nc --arg sha "$commit" --arg short "${commit:0:7}" --arg subject "$subject" \
    '{sha: $sha, short: $short, subject: $subject}' >> "$SUBSTANTIVE_NDJSON"
done

# --- Transient commits: list them ---
for commit in "${TRANSIENT_COMMITS[@]:-}"; do
  [[ -z "$commit" ]] && continue
  subject=$(git log -1 --format="%s" "$commit")
  jq -nc --arg sha "$commit" --arg short "${commit:0:7}" --arg subject "$subject" \
    '{sha: $sha, short: $short, subject: $subject}' >> "$TRANSIENT_NDJSON"
done

# --- Verify transient commits (in an isolated worktree) ---
verify_transient() {
  log_info "Verifying transient commits..."

  if [[ ${#TRANSIENT_COMMITS[@]} -eq 0 ]]; then
    log_info "No transient commits to verify"
    return 0
  fi

  local final_tree expected_tree
  final_tree=$(git rev-parse HEAD^{tree})

  if [[ ${#NON_TRANSIENT_COMMITS[@]} -eq 0 ]]; then
    expected_tree=$(git rev-parse "$MERGE_BASE^{tree}")
  else
    # Cherry-pick non-transient commits onto the merge base in a fresh
    # worktree, then compare the resulting tree against HEAD's tree.
    local wt_path
    wt_path=$(create_worktree "$MERGE_BASE" "transient-replay")

    local cp_failed=false
    for commit in "${NON_TRANSIENT_COMMITS[@]}"; do
      local cp_args=(--no-commit) parent_count
      parent_count=$(git rev-list --parents -n 1 "$commit" | awk '{print NF-1}')
      [[ "$parent_count" -gt 1 ]] && cp_args+=(-m 1)

      local cp_log
      cp_log=$(mktemp)
      local cp_exit=0
      set +e
      ( cd "$wt_path" && git cherry-pick "${cp_args[@]}" "$commit" ) 2>&1 \
        | tee "$cp_log" >&2
      cp_exit=${PIPESTATUS[0]}
      set -e

      if [[ $cp_exit -ne 0 ]]; then
        extract_excerpt "$cp_log"
        TRANSIENT_FAILURE_KIND="cherry_pick_conflict"
        TRANSIENT_FAILED_SHA="$commit"
        TRANSIENT_FAILED_SUBJECT=$(git log -1 --format="%s" "$commit")
        TRANSIENT_OUTPUT_EXCERPT="$EXCERPT"
        TRANSIENT_OUTPUT_TRUNCATED="$EXCERPT_TRUNCATED"
        cp_failed=true
        rm -f "$cp_log"
        ( cd "$wt_path" && git cherry-pick --abort 2>/dev/null || true )
        break
      fi
      rm -f "$cp_log"
    done

    if [[ "$cp_failed" == "true" ]]; then
      remove_worktree "$wt_path"
      return 1
    fi

    expected_tree=$(cd "$wt_path" && git write-tree)
    remove_worktree "$wt_path"
  fi

  if [[ "$final_tree" == "$expected_tree" ]]; then
    log_ok "Transient commits verified: final tree matches (${final_tree:0:12})"
    return 0
  fi

  TRANSIENT_FAILURE_KIND="tree_mismatch"
  extract_diff_excerpt "$expected_tree" "$final_tree"
  TRANSIENT_DIFF_EXCERPT="$DIFF_EXCERPT"
  TRANSIENT_DIFF_TRUNCATED="$DIFF_EXCERPT_TRUNCATED"
  log_error "Transient verification failed: final tree differs from non-transient replay"
  printf %s "$DIFF_EXCERPT" >&2
  return 1
}

# --- Verify a single auto commit in a fresh worktree ---
verify_auto_commit() {
  local commit="$1"
  local subject command short_sha parent expected_tree
  subject=$(git log -1 --format="%s" "$commit")
  command=$(get_auto_command "$subject")
  short_sha="${commit:0:7}"

  log_info "Verifying auto commit $short_sha: $command"

  # Sanity: auto commits are expected to be linear (single parent).
  local parent_count
  parent_count=$(git rev-list --parents -n 1 "$commit" | awk '{print NF-1}')
  if [[ "$parent_count" -ne 1 ]]; then
    log_error "Auto commit $short_sha: expected 1 parent, found $parent_count"
    jq -nc \
      --arg sha "$commit" --arg short "$short_sha" \
      --arg subject "$subject" --arg command "$command" \
      --arg msg "Expected 1 parent, found $parent_count" \
      '{sha: $sha, short: $short, subject: $subject, command: $command, verified: false,
        failure_kind: "command_failed", exit_code: -1, output_excerpt: $msg, output_truncated: false}' \
      >> "$AUTO_NDJSON"
    return 1
  fi

  parent=$(git rev-parse "$commit^")
  expected_tree=$(git rev-parse "$commit^{tree}")

  local wt_path
  wt_path=$(create_worktree "$parent" "auto-${short_sha}")

  # Run the command, streaming combined stdout+stderr to the runner's
  # stderr (so it shows up live in CI logs) while also capturing it to
  # a temp file for the JSON report.
  local out_file
  out_file=$(mktemp)
  local cmd_exit=0
  set +e
  ( cd "$wt_path" && timeout "$TIMEOUT_SECONDS" bash -c "$command" ) 2>&1 \
    | tee "$out_file" >&2
  cmd_exit=${PIPESTATUS[0]}
  set -e

  local emit_failure=""  # one of: "", "timed_out", "command_failed", "tree_mismatch"
  local actual_tree=""

  if [[ $cmd_exit -eq 124 ]]; then
    emit_failure="timed_out"
  elif [[ $cmd_exit -ne 0 ]]; then
    emit_failure="command_failed"
  else
    # Stage all changes (mods, new files, deletions) and compute the tree.
    # `git add -A` respects .gitignore, so we never include build artifacts.
    ( cd "$wt_path" && git add -A )
    actual_tree=$(cd "$wt_path" && git write-tree)
    if [[ "$expected_tree" != "$actual_tree" ]]; then
      emit_failure="tree_mismatch"
    fi
  fi

  if [[ -z "$emit_failure" ]]; then
    log_ok "Auto commit $short_sha verified"
    jq -nc \
      --arg sha "$commit" --arg short "$short_sha" \
      --arg subject "$subject" --arg command "$command" \
      '{sha: $sha, short: $short, subject: $subject, command: $command, verified: true}' \
      >> "$AUTO_NDJSON"
    rm -f "$out_file"
    remove_worktree "$wt_path"
    return 0
  fi

  # Build the failure record. All three branches share the output excerpt
  # (it can be informative even on tree_mismatch); tree_mismatch additionally
  # includes a diff excerpt.
  extract_excerpt "$out_file"
  local out_excerpt="$EXCERPT"
  local out_truncated="$EXCERPT_TRUNCATED"

  local diff_excerpt="" diff_truncated=false
  if [[ "$emit_failure" == "tree_mismatch" ]]; then
    extract_diff_excerpt "$expected_tree" "$actual_tree"
    diff_excerpt="$DIFF_EXCERPT"
    diff_truncated="$DIFF_EXCERPT_TRUNCATED"
    log_error "Auto commit $short_sha: tree mismatch"
  elif [[ "$emit_failure" == "timed_out" ]]; then
    log_error "Auto commit $short_sha: command timed out after ${TIMEOUT_SECONDS}s"
  else
    log_error "Auto commit $short_sha: command failed (exit $cmd_exit)"
  fi

  jq -nc \
    --arg sha "$commit" --arg short "$short_sha" \
    --arg subject "$subject" --arg command "$command" \
    --arg failure_kind "$emit_failure" \
    --argjson exit_code "$cmd_exit" \
    --argjson timeout_seconds "$TIMEOUT_SECONDS" \
    --arg output_excerpt "$out_excerpt" \
    --argjson output_truncated "$out_truncated" \
    --arg diff_excerpt "$diff_excerpt" \
    --argjson diff_truncated "$diff_truncated" \
    '{sha: $sha, short: $short, subject: $subject, command: $command, verified: false,
      failure_kind: $failure_kind}
     + (if $failure_kind == "timed_out" then {timeout_seconds: $timeout_seconds, exit_code: $exit_code}
        elif $failure_kind == "command_failed" then {exit_code: $exit_code}
        else {} end)
     + (if $output_excerpt != "" then {output_excerpt: $output_excerpt, output_truncated: $output_truncated} else {} end)
     + (if $diff_excerpt != "" then {diff_excerpt: $diff_excerpt, diff_truncated: $diff_truncated} else {} end)' \
    >> "$AUTO_NDJSON"

  rm -f "$out_file"
  remove_worktree "$wt_path"
  return 1
}

verify_auto_commits() {
  log_info "Verifying automated commits..."

  if [[ ${#AUTO_COMMITS[@]} -eq 0 ]]; then
    log_info "No automated commits to verify"
    return 0
  fi

  local all_ok=true
  for commit in "${AUTO_COMMITS[@]}"; do
    if ! verify_auto_commit "$commit"; then
      all_ok=false
    fi
  done

  [[ "$all_ok" == "true" ]]
}

# --- Run verifications ---
if ! verify_transient; then
  TRANSIENT_VERIFIED=false
  OVERALL_SUCCESS=false
fi

if ! verify_auto_commits; then
  OVERALL_SUCCESS=false
fi

# --- Output: assemble final JSON ---
build_final_json() {
  jq -nc \
    --argjson success "$([[ "$OVERALL_SUCCESS" == "true" ]] && echo true || echo false)" \
    --argjson transient_verified "$([[ "$TRANSIENT_VERIFIED" == "true" ]] && echo true || echo false)" \
    --slurpfile substantive "$SUBSTANTIVE_NDJSON" \
    --slurpfile auto "$AUTO_NDJSON" \
    --slurpfile transient "$TRANSIENT_NDJSON" \
    --arg transient_failure_kind "$TRANSIENT_FAILURE_KIND" \
    --arg transient_failed_sha "$TRANSIENT_FAILED_SHA" \
    --arg transient_failed_subject "$TRANSIENT_FAILED_SUBJECT" \
    --arg transient_output_excerpt "$TRANSIENT_OUTPUT_EXCERPT" \
    --argjson transient_output_truncated "$TRANSIENT_OUTPUT_TRUNCATED" \
    --arg transient_diff_excerpt "$TRANSIENT_DIFF_EXCERPT" \
    --argjson transient_diff_truncated "$TRANSIENT_DIFF_TRUNCATED" \
    '{success: $success,
      substantive_commits: $substantive,
      auto_commits: $auto,
      transient_commits: $transient,
      transient_verified: $transient_verified}
     + (if $transient_failure_kind != "" then
          {transient_failure_kind: $transient_failure_kind}
          + (if $transient_failed_sha != "" then
               {transient_failed_sha: $transient_failed_sha,
                transient_failed_short: ($transient_failed_sha[0:7]),
                transient_failed_subject: $transient_failed_subject}
             else {} end)
          + (if $transient_output_excerpt != "" then
               {transient_output_excerpt: $transient_output_excerpt,
                transient_output_truncated: $transient_output_truncated}
             else {} end)
          + (if $transient_diff_excerpt != "" then
               {transient_diff_excerpt: $transient_diff_excerpt,
                transient_diff_truncated: $transient_diff_truncated}
             else {} end)
        else {} end)'
}

output_summary() {
  echo ""
  echo "========================================="
  echo "         COMMIT VERIFICATION SUMMARY"
  echo "========================================="
  echo ""

  echo "Substantive commits (${#SUBSTANTIVE_COMMITS[@]}):"
  if [[ ${#SUBSTANTIVE_COMMITS[@]} -eq 0 ]]; then
    echo "  (none)"
  else
    for commit in "${SUBSTANTIVE_COMMITS[@]}"; do
      echo "  - ${commit:0:7}: $(git log -1 --format='%s' "$commit")"
    done
  fi
  echo ""

  echo "Automated commits (${#AUTO_COMMITS[@]}):"
  if [[ ${#AUTO_COMMITS[@]} -eq 0 ]]; then
    echo "  (none)"
  else
    while IFS= read -r row; do
      local short subject verified failure_kind exit_code
      short=$(jq -r '.short' <<<"$row")
      subject=$(jq -r '.subject' <<<"$row")
      verified=$(jq -r '.verified' <<<"$row")
      failure_kind=$(jq -r '.failure_kind // ""' <<<"$row")
      exit_code=$(jq -r '.exit_code // ""' <<<"$row")
      if [[ "$verified" == "true" ]]; then
        log_ok "$short: $subject"
      else
        log_error "$short: $subject"
        case "$failure_kind" in
          timed_out)      echo "        Timed out after ${TIMEOUT_SECONDS}s" ;;
          command_failed) echo "        Command failed (exit $exit_code)" ;;
          tree_mismatch)  echo "        Tree mismatch: command output differs from commit" ;;
        esac
      fi
    done < "$AUTO_NDJSON"
  fi
  echo ""

  echo "Transient commits (${#TRANSIENT_COMMITS[@]}):"
  if [[ ${#TRANSIENT_COMMITS[@]} -eq 0 ]]; then
    echo "  (none)"
  else
    for commit in "${TRANSIENT_COMMITS[@]}"; do
      echo "  - ${commit:0:7}: $(git log -1 --format='%s' "$commit")"
    done
    if [[ "$TRANSIENT_VERIFIED" == "true" ]]; then
      log_ok "Net effect: none (verified)"
    else
      case "$TRANSIENT_FAILURE_KIND" in
        cherry_pick_conflict)
          log_error "Cherry-pick of ${TRANSIENT_FAILED_SHA:0:7} (${TRANSIENT_FAILED_SUBJECT}) failed" ;;
        tree_mismatch)
          log_error "Net effect is non-empty: transient commits modify the final tree" ;;
        *)
          log_error "Verification failed" ;;
      esac
    fi
  fi
  echo ""

  echo "========================================="
  if [[ "$OVERALL_SUCCESS" == "true" ]]; then
    log_ok "ALL VERIFICATIONS PASSED"
  else
    log_error "VERIFICATION FAILED"
  fi
  echo "========================================="
}

if [[ "$JSON_OUTPUT" == "true" ]]; then
  build_final_json
elif [[ -n "$JSON_FILE" ]]; then
  build_final_json > "$JSON_FILE"
  output_summary
else
  output_summary
fi

if [[ "$OVERALL_SUCCESS" == "true" ]]; then
  exit 0
else
  exit 1
fi
