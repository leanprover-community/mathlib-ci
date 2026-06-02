#!/usr/bin/env python3
"""Patch the `#### Declarations diff` section of a PR's `### PR summary`
comment with the post-build, Lean-aware diff.

The section is delimited in the comment by the marker pair below, which
`mathlib4`'s `PR_summary.yml` emits around the declarations-diff part. Because
the markers are always present, patching is a single replace of the region
between them — no header matching, no first-patch special case.

Inputs (environment variables):
    GH_TOKEN        passed through to the `gh` CLI
    REPO            `owner/repo`
    PR_HEAD_SHA     head SHA of the PR (used to resolve PR_NUMBER when unset)
    PR_NUMBER       PR number (optional; resolved from PR_HEAD_SHA when empty)
    MODE            `success` (default) or `warning`
    OVERRIDE_FILE   success mode: path to the rendered section produced by
                      `declsDiff.py --with-heading`
    DEFAULT_BRANCH  warning mode: branch named in the rebase hint (default `master`)

Modes:
    success — replace the marked region with the rendered Lean-aware section.
    warning — append a cache-miss notice inside the region, leaving the
              pre-build (regex) diff visible. Idempotent via WARNING_MARKER.

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

BEGIN = "<!-- DECLS_DIFF_BEGIN -->"
END = "<!-- DECLS_DIFF_END -->"
WARNING_MARKER = "<!-- DECLS_DIFF_WARNING -->"
PR_SUMMARY_PREFIX = "### PR summary"

_REGION_RE = re.compile(re.escape(BEGIN) + r"(.*?)" + re.escape(END), re.DOTALL)


def build_warning(default_branch: str) -> str:
    """Render the cache-miss notice appended in `warning` mode."""
    return "\n".join([
        WARNING_MARKER,
        "",
        f"> ⚠️ **Lean-aware diff unavailable** — the Mathlib cache for this PR's "
        f"merge-base isn't on the server (typically a bors-batch intermediate that "
        f"CI never built). Merge `{default_branch}` into this PR and push to refresh.",
    ])


def splice_success(body: str, section: str) -> str:
    """Replace the marked region with `section` (a complete, heading-wrapped
    `#### Declarations diff` block). Returns `body` unchanged when the markers
    are absent."""
    if not _REGION_RE.search(body):
        return body
    region = f"{BEGIN}\n{section.strip()}\n{END}"
    return _REGION_RE.sub(lambda _m: region, body, count=1)


def splice_warning(body: str, warning_md: str) -> str:
    """Append `warning_md` inside the marked region, keeping its existing
    content. Idempotent: a region already carrying WARNING_MARKER is left
    unchanged. Returns `body` unchanged when the markers are absent."""
    m = _REGION_RE.search(body)
    if m is None:
        return body
    inner = m.group(1)
    if WARNING_MARKER in inner:
        return body
    new_inner = inner.rstrip() + "\n\n" + warning_md + "\n"
    return body[: m.start()] + BEGIN + new_inner + END + body[m.end():]


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
        comments = gh_json(f"repos/{repo}/issues/{pr}/comments")
    except subprocess.CalledProcessError as e:
        print(f"updateDeclsDiffSection: gh api failed: {e.stderr}", file=sys.stderr)
        return 1
    target = next((c for c in comments if c["body"].startswith(PR_SUMMARY_PREFIX)), None)
    if target is None:
        print(f"updateDeclsDiffSection: no '{PR_SUMMARY_PREFIX}' comment on PR #{pr}; "
              "pre-build workflow may not have run yet — skipping.")
        return 0

    if mode == "warning":
        new_body = splice_warning(
            target["body"], build_warning(os.environ.get("DEFAULT_BRANCH", "master")))
    else:
        override = os.environ.get("OVERRIDE_FILE", "")
        section = Path(override).read_text() if override and Path(override).is_file() else ""
        if not section.strip():
            print("updateDeclsDiffSection: empty OVERRIDE_FILE; nothing to patch.")
            return 0
        new_body = splice_success(target["body"], section)

    if new_body == target["body"]:
        print(f"updateDeclsDiffSection: comment already up-to-date (mode={mode}).")
        return 0

    print(f"updateDeclsDiffSection: patching comment id={target['id']} on PR #{pr} (mode={mode})")
    return _patch_comment(repo, target["id"], new_body)


if __name__ == "__main__":
    sys.exit(main())
