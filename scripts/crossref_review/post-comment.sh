#!/usr/bin/env bash
#
# Cross-reference review orchestrator.
#
# Invoked from mathlib4's .github/workflows/crossref_review.yml after the
# privilege-escalation-bridge has delivered the TSV produced by
# scripts/dump_crossref_tags.lean.
#
# Pipeline:
#   1. Validate the bridge payload (PR_NUMBER decimal; HEAD_SHA hex SHA;
#      TSV present and under MAX_TSV_BYTES).
#   2. Skip if the PR head has moved since the build that produced our TSV
#      (force-push between build and comment).
#   3. Get the PR's merge-base commit on master and, if there's a master CI
#      run for that exact SHA, download its `crossref-tags-baseline` artifact.
#      Missing baseline is non-fatal — we just don't subtract anything.
#   4. Get the PR's changed .lean files via `gh pr diff --name-only` and
#      filter the TSV by them. If nothing matches, exit clean.
#   5. Validate every surviving tag against a per-database regex (defence
#      in depth against a producer that bypasses mathlib's attribute parser).
#      Cap row count after filtering.
#   6. Resolve crossref-render: prefer $CROSSREF_RENDER_BIN; otherwise
#      clone external-tags at the pinned SHA and build it.
#   7. Run crossref-render under a wall-clock timeout, with --strict (fail
#      on malformed rows) and --baseline-tsv (subtract baseline rows).
#   8. Hand off the rendered Markdown to update_PR_comment.sh.
#   9. Exit non-zero iff any tag was reported missing upstream.
#
# Required env vars:
#   GH_TOKEN     GitHub token with pull-requests:write
#   PR_NUMBER    PR to comment on  (validated /^[0-9]+$/)
#   HEAD_SHA     PR head SHA the TSV was built from  (/^[0-9a-f]{40}$/)
#   TSV_PATH     Absolute path to crossref-tags.tsv (delivered under .bridge/)
#
# Optional env vars:
#   CROSSREF_RENDER_BIN   Path to a prebuilt crossref-render binary.

set -euo pipefail
IFS=$'\n\t'

# --- pinned external-tags ref ---------------------------------------------
#
# Bump when external-tags has a change you trust. Reviewers of the bump PR
# can diff external-tags@<OLD>..<NEW>.

EXTERNAL_TAGS_REPO="leanprover-community/external-tags"
EXTERNAL_TAGS_SHA="4634089069155423f802905f8d649758222e07b5"

# Hard cap on what we'll parse from the bridge artifact. The producer in
# mathlib4's scripts/dump_crossref_tags.lean caps at 2 MB defensively, but
# THAT script is PR-controlled and a malicious PR can remove the cap, so
# the trusted cap is enforced again here. 4 MB is ample over the current
# ~55 KB / 491-row population.
MAX_TSV_BYTES=$((4 * 1024 * 1024))

# Cap on rows after the changed-files + baseline filters. With the
# baseline filter, a "normal" PR has at most a handful of rows. We allow
# orders-of-magnitude more in case a PR genuinely adds many new tags.
MAX_FILTERED_ROWS=500

# Wall-clock cap on the renderer (fetches snippets from external APIs).
# Each Gerby fetch is one HTTP request with a 10s timeout; this caps total
# render time regardless of row count or upstream latency.
RENDER_TIMEOUT_SECONDS=180

# --- env validation --------------------------------------------------------

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${PR_NUMBER:?PR_NUMBER is required}"
: "${HEAD_SHA:?HEAD_SHA is required}"
: "${TSV_PATH:?TSV_PATH is required}"

if ! [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "PR_NUMBER must be decimal digits (got: $PR_NUMBER)" >&2
  exit 1
fi
if ! [[ "$HEAD_SHA" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "HEAD_SHA must be a 40-char hex SHA (got: $HEAD_SHA)" >&2
  exit 1
fi

if [[ ! -f "$TSV_PATH" ]]; then
  echo "TSV not found at $TSV_PATH; nothing to do." >&2
  exit 0
fi

TSV_SIZE="$(stat -c%s "$TSV_PATH" 2>/dev/null || stat -f%z "$TSV_PATH")"
if (( TSV_SIZE > MAX_TSV_BYTES )); then
  echo "TSV at $TSV_PATH is $TSV_SIZE bytes, exceeds cap of $MAX_TSV_BYTES." >&2
  echo "Refusing to parse — investigate dump_crossref_tags.lean changes." >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MATHLIB_CI_ROOT="$(cd "$HERE/../.." && pwd)"
REPO="${GITHUB_REPOSITORY:-leanprover-community/mathlib4}"

WORK_DIR="$(mktemp -d -t crossref-review-XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

# Marker on the first line of the rendered comment; identifies our prior
# comment when we look it up to update/delete.
MARKER="<!-- external-tags:crossref-review -->"

# Return the numeric ID of the existing bot comment with our marker (if any),
# or empty.
find_existing_comment_id() {
  gh api "repos/$REPO/issues/$PR_NUMBER/comments" --paginate \
    --jq ".[] | select(.body | startswith(\"$MARKER\")) | .id" 2>/dev/null | head -1
}

# Delete the prior bot comment if one exists. Used on clean-exit paths
# where this PR has nothing to comment about — without this, a comment
# from an earlier build of the same PR would linger and mislead the
# reviewer about the current state.
delete_existing_comment_if_any() {
  local id
  id="$(find_existing_comment_id)"
  if [[ -n "$id" ]]; then
    echo "Deleting orphaned bot comment (id=$id)"
    gh api -X DELETE "repos/$REPO/issues/comments/$id" >/dev/null
  fi
}

# --- step 2: stale-run check ----------------------------------------------

# If the PR's head SHA has moved since the build that produced our TSV,
# our comment would be misleading (it'd describe an old state of the PR).
# Exit cleanly — a fresher build will produce a fresher comment.
CURRENT_HEAD_SHA="$(gh pr view "$PR_NUMBER" --repo "$REPO" \
  --json headRefOid --jq '.headRefOid' 2>/dev/null || true)"
if [[ -z "$CURRENT_HEAD_SHA" ]]; then
  echo "Could not look up PR #$PR_NUMBER head; aborting." >&2
  exit 1
fi
if [[ "$CURRENT_HEAD_SHA" != "$HEAD_SHA" ]]; then
  echo "PR #$PR_NUMBER head has moved ($HEAD_SHA -> $CURRENT_HEAD_SHA); skipping stale review."
  exit 0
fi

# --- step 3: merge-base baseline TSV --------------------------------------

# Find the PR's merge-base on master via the GitHub compare API.
BASE_REF="$(gh pr view "$PR_NUMBER" --repo "$REPO" \
  --json baseRefName --jq '.baseRefName' 2>/dev/null || true)"
BASELINE_TSV=""
if [[ -n "$BASE_REF" ]]; then
  MERGE_BASE="$(gh api "repos/$REPO/compare/$BASE_REF...$HEAD_SHA" \
    --jq '.merge_base_commit.sha // empty' 2>/dev/null || true)"
  if [[ -n "$MERGE_BASE" ]]; then
    # Find a successful CI run at that exact merge-base SHA. We don't
    # filter by --branch: the dump output is a pure function of the
    # elaborated environment at the SHA, so any branch that ran CI on
    # that commit will have an equivalent baseline artifact (useful for
    # PRs that target non-master branches).
    for wf in "continuous integration" "continuous integration (mathlib forks)"; do
      RUN_ID="$(gh run list --repo "$REPO" --workflow="$wf" \
        --status success --commit "$MERGE_BASE" --limit 1 \
        --json databaseId --jq '.[0].databaseId // empty' 2>/dev/null || true)"
      if [[ -n "$RUN_ID" ]]; then break; fi
    done
    if [[ -n "$RUN_ID" ]]; then
      BASELINE_DIR="$WORK_DIR/baseline"
      mkdir -p "$BASELINE_DIR"
      if gh run download "$RUN_ID" --repo "$REPO" \
         --name crossref-tags-baseline --dir "$BASELINE_DIR" 2>/dev/null \
         && [[ -f "$BASELINE_DIR/crossref-tags.tsv" ]]; then
        BASELINE_TSV="$BASELINE_DIR/crossref-tags.tsv"
        echo "Got baseline TSV from master run $RUN_ID (commit $MERGE_BASE)."
      fi
    fi
  fi
fi
if [[ -z "$BASELINE_TSV" ]]; then
  echo "Baseline TSV unavailable (merge-base build expired or missing); \
rendering against changed-files filter only."
fi

# --- step 4: filter TSV by changed files ----------------------------------

echo "Fetching list of changed files for PR #$PR_NUMBER ..."
RAW_CHANGED="$WORK_DIR/changed-raw.txt"
if ! gh pr diff "$PR_NUMBER" --name-only --repo "$REPO" > "$RAW_CHANGED"; then
  echo "gh pr diff failed for PR #$PR_NUMBER; aborting." >&2
  exit 1
fi
grep -E '\.lean$' "$RAW_CHANGED" > "$WORK_DIR/changed.txt" || true

if [[ ! -s "$WORK_DIR/changed.txt" ]]; then
  echo "PR #$PR_NUMBER touched no .lean files; nothing to comment."
  delete_existing_comment_if_any
  exit 0
fi

# Filter TSV: keep rows whose 4th field (module path) is in the changed set.
awk -F'\t' -v OFS='\t' '
  NR==FNR { files[$0]=1; next }
  files[$4]
' "$WORK_DIR/changed.txt" "$TSV_PATH" > "$WORK_DIR/filtered.tsv"

# --- step 5: per-tag regex validation -------------------------------------

# Reject any row whose tag doesn't match its database's regex. This is
# defence in depth: the master attribute parsers enforce the same shapes,
# but the producer is PR-controlled.
VALIDATED="$WORK_DIR/validated.tsv"
: > "$VALIDATED"
while IFS=$'\t' read -r db tag _decl _file _comment; do
  case "$db" in
    stacks|kerodon)
      [[ "$tag" =~ ^[0-9A-Z]{4}$ ]] || { echo "rejecting malformed $db tag: $tag" >&2; continue; }
      ;;
    wikidata)
      [[ "$tag" =~ ^Q[0-9]+$ ]] || { echo "rejecting malformed wikidata tag: $tag" >&2; continue; }
      ;;
    *)
      echo "rejecting unknown database: $db" >&2
      continue
      ;;
  esac
  printf '%s\t%s\t%s\t%s\t%s\n' "$db" "$tag" "$_decl" "$_file" "$_comment"
done < "$WORK_DIR/filtered.tsv" >> "$VALIDATED"

# --- step 5b: subtract baseline (rows that already existed at the merge-base) -

# Apply this BEFORE the row-count cap so a maintenance PR that touches
# many files containing existing tags isn't rejected for "too many rows"
# when the baseline would have subtracted them all anyway.
RENDER_TSV="$VALIDATED"
if [[ -n "$BASELINE_TSV" ]]; then
  awk 'NR==FNR { seen[$0]=1; next } !seen[$0]' \
      "$BASELINE_TSV" "$VALIDATED" > "$WORK_DIR/diffed.tsv"
  RENDER_TSV="$WORK_DIR/diffed.tsv"
fi

ROW_COUNT="$(wc -l < "$RENDER_TSV" | tr -d ' ')"
if [[ -n "$BASELINE_TSV" ]]; then
  echo "After baseline subtraction: $ROW_COUNT row(s) to render."
else
  echo "No baseline; rendering $ROW_COUNT validated row(s)."
fi

if [[ "$ROW_COUNT" -eq 0 ]]; then
  echo "No new or changed cross-reference tags introduced by this PR."
  delete_existing_comment_if_any
  exit 0
fi

if (( ROW_COUNT > MAX_FILTERED_ROWS )); then
  echo "Render set has $ROW_COUNT rows, exceeds cap of $MAX_FILTERED_ROWS." >&2
  echo "Refusing to render — investigate dump_crossref_tags.lean changes." >&2
  exit 1
fi

# --- step 6: resolve crossref-render ---------------------------------------

# Prefer a prebuilt binary passed in via env (the workflow caches and
# pre-builds it). Fall back to building from a clone here, so this script
# stays runnable standalone (developers, ad-hoc invocations).
RENDER_BIN=""
if [[ -n "${CROSSREF_RENDER_BIN:-}" && -x "$CROSSREF_RENDER_BIN" ]]; then
  echo "Using prebuilt crossref-render: $CROSSREF_RENDER_BIN"
  RENDER_BIN="$CROSSREF_RENDER_BIN"
else
  echo "CROSSREF_RENDER_BIN not set; building crossref-render from $EXTERNAL_TAGS_REPO@$EXTERNAL_TAGS_SHA"
  if ! command -v elan >/dev/null 2>&1; then
    echo "Installing elan ..."
    curl -sSfL https://elan.lean-lang.org/elan-init.sh \
      | bash -s -- -y --default-toolchain none
    export PATH="$HOME/.elan/bin:$PATH"
  fi
  EXT_DIR="$WORK_DIR/external-tags"
  echo "Cloning $EXTERNAL_TAGS_REPO at $EXTERNAL_TAGS_SHA ..."
  git clone --quiet "https://github.com/$EXTERNAL_TAGS_REPO" "$EXT_DIR"
  ( cd "$EXT_DIR" && git checkout --quiet "$EXTERNAL_TAGS_SHA" )
  echo "Building crossref-render ..."
  ( cd "$EXT_DIR" && lake build crossref-render )
  RENDER_BIN="$EXT_DIR/.lake/build/bin/crossref-render"
fi

if [[ ! -x "$RENDER_BIN" ]]; then
  echo "crossref-render binary not found at $RENDER_BIN" >&2
  exit 1
fi

# --- step 7: render the Markdown comment -----------------------------------

COMMENT_FILE="$WORK_DIR/comment.md"
# We've already filtered by changed files and subtracted the baseline in
# the shell above (so the row-count cap is applied to what actually gets
# rendered, not the pre-subtraction set). The renderer just walks the
# TSV and fetches snippets — no further filtering needed here.
RENDER_ARGS=(
  --tsv "$RENDER_TSV"
  --strict
  --out "$COMMENT_FILE"
)

RENDER_EXIT=0
timeout --signal=TERM "$RENDER_TIMEOUT_SECONDS" "$RENDER_BIN" "${RENDER_ARGS[@]}" \
  || RENDER_EXIT=$?

# `timeout` returns 124 on TERM, 137 on KILL.
case "$RENDER_EXIT" in
  0)
    echo "crossref-render reported nothing to comment; exiting clean."
    delete_existing_comment_if_any
    exit 0
    ;;
  1|2) : ;;  # comment was written; fall through
  124|137)
    echo "crossref-render exceeded ${RENDER_TIMEOUT_SECONDS}s timeout; aborting." >&2
    exit 1
    ;;
  65)
    echo "crossref-render rejected the TSV as malformed (--strict)." >&2
    exit 1
    ;;
  *)
    echo "crossref-render failed with exit $RENDER_EXIT" >&2
    exit "$RENDER_EXIT"
    ;;
esac

if [[ ! -f "$COMMENT_FILE" ]]; then
  echo "crossref-render did not write $COMMENT_FILE" >&2
  exit 1
fi

# --- step 8: post or update the bot comment --------------------------------

# Skip the update if the existing bot comment's body is byte-identical to
# what we'd post. Avoids the notification each PR push would otherwise
# generate for an unchanged comment.
EXISTING_ID="$(find_existing_comment_id)"
SKIP_UPDATE=0
if [[ -n "$EXISTING_ID" ]]; then
  EXISTING_BODY="$(gh api "repos/$REPO/issues/comments/$EXISTING_ID" --jq '.body' 2>/dev/null || true)"
  NEW_BODY="$(cat "$COMMENT_FILE")"
  if [[ "$EXISTING_BODY" == "$NEW_BODY" ]]; then
    echo "Bot comment body unchanged; skipping update to avoid notification."
    SKIP_UPDATE=1
  fi
fi

if [[ "$SKIP_UPDATE" -eq 0 ]]; then
  export GITHUB_REPOSITORY="$REPO"
  bash "$MATHLIB_CI_ROOT/scripts/pr_summary/update_PR_comment.sh" \
    "$COMMENT_FILE" \
    "$MARKER" \
    "$PR_NUMBER"
fi

# --- step 9: fail the check if any tag is missing -------------------------

# Authoritative signal is crossref-render's exit code (1 = missing).
if [[ "$RENDER_EXIT" -eq 1 ]]; then
  echo "crossref-render reported missing tags; failing the check."
  exit 1
fi

echo "All tags resolved cleanly."
exit 0
