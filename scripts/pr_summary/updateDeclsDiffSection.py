#!/usr/bin/env python3
"""Patch the `#### Declarations diff (Lean)` section of a PR's `### PR summary`
comment with the post-build, Lean-aware diff.

The comment carries two declarations-diff blocks emitted by `mathlib4`'s
`PR_summary.yml`: the regex `#### Declarations diff (regex)` block (owned by
`PR_summary.yml`) and a `#### Declarations diff (Lean -- pending)` placeholder
delimited by the marker pair below. This script touches only the marked Lean
region; the regex block is left untouched. Because the markers are always
present, patching is a single replace of the region between them — no header
matching, no first-patch special case.

Inputs (environment variables):
    GH_TOKEN        passed through to the `gh` CLI
    REPO            `owner/repo`
    PR_HEAD_SHA     head SHA of the PR (used to resolve PR_NUMBER when unset)
    PR_NUMBER       PR number (optional; resolved from PR_HEAD_SHA when empty)
    MODE            `success` (default) or `warning`
    OVERRIDE_FILE   success mode: path to the rendered section produced by
                      `declsDiff.py --with-heading`
    DEFAULT_BRANCH  warning mode: branch named in the rebase hint (default `master`)

Modes (both replace the Lean region wholesale):
    success — the rendered Lean-aware section (heading `(Lean)`).
    warning — a cache-miss block (heading `(Lean -- unavailable)`); the regex
              block elsewhere in the comment stays visible.

Exit codes:
    0 — patched, or nothing to do (no summary comment / no markers / unchanged)
    1 — non-recoverable error (gh CLI failure, malformed env)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

LEAN_BEGIN = "<!-- DECLS_DIFF_LEAN_BEGIN -->"
LEAN_END = "<!-- DECLS_DIFF_LEAN_END -->"
PR_SUMMARY_PREFIX = "### PR summary"

_REGION_RE = re.compile(re.escape(LEAN_BEGIN) + r"(.*?)" + re.escape(LEAN_END), re.DOTALL)


def build_warning(default_branch: str) -> str:
    """Render the cache-miss block that replaces the Lean region on a miss.
    The heading carries the `(Lean -- unavailable)` status."""
    return "\n".join([
        "#### Declarations diff (Lean -- unavailable)",
        "",
        f"> ⚠️ The Mathlib cache for this PR's merge-base isn't on the server "
        f"(typically a bors-batch intermediate that CI never built). "
        f"Merge `{default_branch}` into this PR and push to refresh.",
    ])


def splice(body: str, block: str) -> str:
    """Replace the Lean-marked region's content with `block`. Returns `body`
    unchanged when the markers are absent. Idempotent for a given `block`."""
    if not _REGION_RE.search(body):
        return body
    region = f"{LEAN_BEGIN}\n{block.strip()}\n{LEAN_END}"
    return _REGION_RE.sub(lambda _m: region, body, count=1)


def find_summary_comment(comments: list[dict]) -> dict | None:
    """Return the first comment whose body starts with `### PR summary`, else
    `None`. Tolerates a `null` body (the GitHub API permits it) by treating it
    as empty rather than raising."""
    return next(
        (c for c in comments if (c.get("body") or "").startswith(PR_SUMMARY_PREFIX)),
        None,
    )


def gh_json(*args: str) -> object:
    out = subprocess.run(
        ["gh", "api", *args], check=True, text=True, capture_output=True,
    ).stdout
    return json.loads(out)


def resolve_pr(repo: str, head_sha: str) -> int | None:
    """Return the PR number from PR_NUMBER, else look it up from the head SHA.
    The SHA lookup misses fork PRs, so the workflow passes PR_NUMBER for those."""
    pr_env = os.environ.get("PR_NUMBER", "").strip()
    if pr_env:
        try:
            return int(pr_env)
        except ValueError:
            print(f"updateDeclsDiffSection: invalid PR_NUMBER={pr_env!r}", file=sys.stderr)
            return None
    pulls = gh_json(f"repos/{repo}/commits/{head_sha}/pulls")
    return pulls[0]["number"] if pulls else None


def _patch_comment(repo: str, comment_id: int, new_body: str) -> int:
    proc = subprocess.run(
        ["gh", "api", "-X", "PATCH",
         f"repos/{repo}/issues/comments/{comment_id}", "--input", "-"],
        input=json.dumps({"body": new_body}), text=True, capture_output=True,
    )
    if proc.returncode != 0:
        print(f"updateDeclsDiffSection: PATCH failed: {proc.stderr}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    repo = os.environ["REPO"]
    head_sha = os.environ.get("PR_HEAD_SHA", "")
    mode = os.environ.get("MODE", "success")

    try:
        pr = resolve_pr(repo, head_sha)
    except subprocess.CalledProcessError as e:
        print(f"updateDeclsDiffSection: gh api failed: {e.stderr}", file=sys.stderr)
        return 1
    if pr is None:
        print(f"updateDeclsDiffSection: no PR for {head_sha} (set PR_NUMBER); nothing to patch.")
        return 0

    try:
        # `--paginate` so the summary comment is found even on PRs with more
        # comments than fit on the first API page.
        comments = gh_json("--paginate", f"repos/{repo}/issues/{pr}/comments")
    except subprocess.CalledProcessError as e:
        print(f"updateDeclsDiffSection: gh api failed: {e.stderr}", file=sys.stderr)
        return 1
    target = find_summary_comment(comments)
    if target is None:
        print(f"updateDeclsDiffSection: no '{PR_SUMMARY_PREFIX}' comment on PR #{pr}; "
              "pre-build workflow may not have run yet — skipping.")
        return 0

    if mode == "warning":
        block = build_warning(os.environ.get("DEFAULT_BRANCH", "master"))
    else:
        override = os.environ.get("OVERRIDE_FILE", "")
        block = Path(override).read_text() if override and Path(override).is_file() else ""
        if not block.strip():
            print("updateDeclsDiffSection: empty OVERRIDE_FILE; nothing to patch.")
            return 0
    new_body = splice(target["body"], block)

    if new_body == target["body"]:
        print(f"updateDeclsDiffSection: comment already up-to-date (mode={mode}).")
        return 0

    print(f"updateDeclsDiffSection: patching comment id={target['id']} on PR #{pr} (mode={mode})")
    return _patch_comment(repo, target["id"], new_body)


if __name__ == "__main__":
    sys.exit(main())
