#!/usr/bin/env bash
#
# Cross-reference review orchestrator.
#
# Invoked from mathlib4's .github/workflows/crossref_review.yml after the
# privilege-escalation-bridge has delivered the TSV produced by
# scripts/dump_crossref_tags.lean.
#
# What it does:
#   1. Validate the bridge payload (PR_NUMBER is decimal; HEAD_SHA is a SHA;
#      TSV exists and is under MAX_TSV_BYTES).
#   2. Refuse to post if the PR has moved since the build that produced the
#      TSV (force-push between build and comment).
#   3. Ask GitHub which .lean files this PR touched, and filter the TSV
#      down to records whose source module matches one of them. If nothing
#      matches, exit clean.
#   4. Resolve crossref-render: prefer $CROSSREF_RENDER_BIN if set (the
#      workflow pre-builds and caches it), otherwise clone external-tags
#      at the pinned SHA and `lake build crossref-render` ourselves.
#   5. Run crossref-render; capture the Markdown body.
#   6. Use mathlib-ci's existing update_PR_comment.sh to post-or-update the
#      bot comment on the PR (deduped by the marker on the first line).
#   7. Exit non-zero iff crossref-render reported missing tags (exit 1).
#
# Required env vars:
#   GH_TOKEN     GitHub token with pull-requests:write
#   PR_NUMBER    The PR to comment on  (validated /^[0-9]+$/)
#   HEAD_SHA     The PR head SHA the TSV was built from  (validated /^[0-9a-f]{40}$/)
#   TSV_PATH     Absolute path to crossref-tags.tsv (delivered under .bridge/)
#
# Optional env vars:
#   CROSSREF_RENDER_BIN   Path to a prebuilt crossref-render binary. If set,
#                         we skip the clone+build and use this instead.

set -euo pipefail
IFS=$'\n\t'

# --- pinned external-tags ref ---------------------------------------------
#
# Bump when external-tags has a change you trust. Reviewers of the bump PR
# can diff external-tags@<OLD>..<NEW>.

EXTERNAL_TAGS_REPO="leanprover-community/external-tags"
EXTERNAL_TAGS_SHA="f56909c70b3ed7cc607a6110c04f25bd19d55731"

# Hard cap on what we'll parse from the bridge artifact. The producer in
# mathlib4's scripts/dump_crossref_tags.lean caps at 2 MB defensively, but
# THAT script is PR-controlled (it lives in scripts/ and a malicious PR can
# remove the cap), so the cap must be enforced again here, in trusted code.
# 4 MB is comfortable headroom over the current ~55 KB population.
MAX_TSV_BYTES=$((4 * 1024 * 1024))

# --- env vars + validation -------------------------------------------------

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

# Where we resolve paths relative to.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MATHLIB_CI_ROOT="$(cd "$HERE/../.." && pwd)"
REPO="${GITHUB_REPOSITORY:-leanprover-community/mathlib4}"

WORK_DIR="$(mktemp -d -t crossref-review-XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

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

# --- step 3: filter TSV by changed files ----------------------------------

echo "Fetching list of changed files for PR #$PR_NUMBER ..."
# Capture into a temp file so we can distinguish "gh failed" from "no
# .lean files in the diff" — piping straight through `grep || true` would
# eat both.
RAW_CHANGED="$WORK_DIR/changed-raw.txt"
if ! gh pr diff "$PR_NUMBER" --name-only --repo "$REPO" > "$RAW_CHANGED"; then
  echo "gh pr diff failed for PR #$PR_NUMBER; aborting." >&2
  exit 1
fi
grep -E '\.lean$' "$RAW_CHANGED" > "$WORK_DIR/changed.txt" || true

if [[ ! -s "$WORK_DIR/changed.txt" ]]; then
  echo "PR #$PR_NUMBER touched no .lean files; nothing to comment."
  exit 0
fi

# Filter TSV: keep rows whose 4th field (module path) is in the changed set.
awk -F'\t' -v OFS='\t' '
  NR==FNR { files[$0]=1; next }
  files[$4]
' "$WORK_DIR/changed.txt" "$TSV_PATH" > "$WORK_DIR/filtered.tsv"

ROW_COUNT="$(wc -l < "$WORK_DIR/filtered.tsv" | tr -d ' ')"
echo "Filtered TSV has $ROW_COUNT row(s)."

if [[ "$ROW_COUNT" -eq 0 ]]; then
  echo "No cross-reference tags in files this PR touched; nothing to comment."
  exit 0
fi

# --- step 4: resolve crossref-render ---------------------------------------

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

# --- step 5: render the Markdown comment -----------------------------------

COMMENT_FILE="$WORK_DIR/comment.md"
RENDER_EXIT=0
"$RENDER_BIN" \
  --tsv "$WORK_DIR/filtered.tsv" \
  --changed-files "$WORK_DIR/changed.txt" \
  --out "$COMMENT_FILE" \
  || RENDER_EXIT=$?

# crossref-render exits 0 = nothing to comment, 1 = some missing, 2 = all ok.
# Anything else is an error.
case "$RENDER_EXIT" in
  0)
    echo "crossref-render reported nothing to comment; exiting clean."
    exit 0
    ;;
  1|2)
    : # fall through
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

# --- step 6: post or update the bot comment --------------------------------

# The marker is the first line of the rendered comment and matches the one
# baked into Crossrefs/Render.lean in external-tags. update_PR_comment.sh
# matches comments by `startswith($cID)`, so the marker on line 1 is enough.
MARKER="<!-- external-tags:crossref-review -->"

export GITHUB_REPOSITORY="$REPO"
bash "$MATHLIB_CI_ROOT/scripts/pr_summary/update_PR_comment.sh" \
  "$COMMENT_FILE" \
  "$MARKER" \
  "$PR_NUMBER"

# --- step 7: fail the check if any tag is missing --------------------------

# Authoritative signal is crossref-render's exit code (1 = missing).
# Greping the rendered Markdown for a magic string would let PR authors
# force-red the check via crafted tag comments.
if [[ "$RENDER_EXIT" -eq 1 ]]; then
  echo "crossref-render reported missing tags; failing the check."
  exit 1
fi

echo "All tags resolved cleanly."
exit 0
