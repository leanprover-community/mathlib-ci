#!/usr/bin/env bash
#
# Cross-reference review orchestrator.
#
# Invoked from mathlib4's .github/workflows/crossref_review.yml after the
# privilege-escalation-bridge has delivered the TSV produced by
# scripts/dump_crossref_tags.lean.
#
# What it does:
#   1. Ask GitHub which .lean files this PR touched, and filter the TSV down
#      to records whose source module matches one of them. If nothing
#      matches, we have nothing to comment about — exit clean.
#   2. Install elan, clone leanprover-community/external-tags at the pinned
#      SHA below, build the crossref-render lake exe.
#   3. Run crossref-render on the filtered TSV; capture the Markdown body.
#   4. Use mathlib-ci's existing update_PR_comment.sh to post-or-update the
#      bot comment on the PR (deduped by the marker on the first line).
#   5. Exit non-zero iff at least one tag was reported missing upstream, so
#      crossref_review.yml's check turns red on the PR.
#
# Required env vars:
#   GH_TOKEN     GitHub token with pull-requests:write
#   PR_NUMBER    The PR to comment on
#   HEAD_SHA     The PR head SHA the TSV was built from (informational only)
#   TSV_PATH     Absolute path to crossref-tags.tsv (delivered under .bridge/)
#
# Pin: update EXTERNAL_TAGS_SHA when external-tags has a change you trust.
# Bumping it is a one-line PR to mathlib-ci; reviewers can diff the upstream
# commits at the new SHA.

set -euo pipefail
IFS=$'\n\t'

# --- pinned external-tags ref ---------------------------------------------

EXTERNAL_TAGS_REPO="leanprover-community/external-tags"
EXTERNAL_TAGS_SHA="62b7e270d046380e1cbfaeec4d74d5333c63d7e5"

# --- env vars --------------------------------------------------------------

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${PR_NUMBER:?PR_NUMBER is required}"
: "${HEAD_SHA:?HEAD_SHA is required}"
: "${TSV_PATH:?TSV_PATH is required}"

if [[ ! -f "$TSV_PATH" ]]; then
  echo "TSV not found at $TSV_PATH; nothing to do." >&2
  exit 0
fi

# Where we resolve paths relative to.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MATHLIB_CI_ROOT="$(cd "$HERE/../.." && pwd)"

WORK_DIR="$(mktemp -d -t crossref-review-XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

# --- step 1: filter TSV by changed files ----------------------------------

echo "Fetching list of changed files for PR #$PR_NUMBER ..."
gh pr diff "$PR_NUMBER" --name-only \
  --repo "${GITHUB_REPOSITORY:-leanprover-community/mathlib4}" \
  | grep -E '\.lean$' \
  > "$WORK_DIR/changed.txt" || true

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
  # Still consider deleting any prior bot comment so a stale one doesn't linger.
  # (Left as future work — for now we just do nothing.)
  exit 0
fi

# --- step 2: install elan + build crossref-render --------------------------

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

# --- step 3: render the Markdown comment -----------------------------------

COMMENT_FILE="$WORK_DIR/comment.md"
RENDER_EXIT=0
( cd "$EXT_DIR" && lake exe crossref-render \
    --tsv "$WORK_DIR/filtered.tsv" \
    --out "$COMMENT_FILE" ) || RENDER_EXIT=$?

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

# --- step 4: post or update the bot comment --------------------------------

# The marker is the first line of the rendered comment and matches the one
# baked into Crossrefs/Render.lean in external-tags. update_PR_comment.sh
# matches comments by `startswith($cID)`, so the marker on line 1 is enough.
MARKER="<!-- external-tags:crossref-review -->"

export GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-leanprover-community/mathlib4}"
bash "$MATHLIB_CI_ROOT/scripts/pr_summary/update_PR_comment.sh" \
  "$COMMENT_FILE" \
  "$MARKER" \
  "$PR_NUMBER"

# --- step 5: fail the check if any tag is missing --------------------------

# Re-grep the rendered body for the "missing" marker rendered by external-tags.
if grep -q '\*\*missing\*\*' "$COMMENT_FILE"; then
  echo "At least one tag was reported missing upstream; failing the check."
  exit 1
fi

echo "All tags resolved cleanly."
exit 0
