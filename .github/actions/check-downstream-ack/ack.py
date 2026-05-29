"""Render and POST the acknowledgement comment after a `!downstream-check` dispatch.

One ack per dispatch (no edit-in-place): it confirms the requested
entries, the merge ref, and links to the dispatch run. Multiple
directives on a PR leave separate ack lines, preserving the audit trail.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, help="`owner/repo` of the PR.")
    p.add_argument("--pr-number", required=True)
    p.add_argument(
        "--downstreams",
        required=True,
        help="Comma-separated normalised entries (from check-downstream-validate's `resolved_names`).",
    )
    p.add_argument(
        "--merge-sha",
        required=True,
        help="Resolved `merge_commit_sha` (rendered as a short SHA).",
    )
    p.add_argument(
        "--run-url",
        required=True,
        help="Link to the dispatching workflow run, so the requester can follow progress.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    body = render(
        downstreams=args.downstreams,
        merge_sha=args.merge_sha,
        run_url=args.run_url,
    )
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("::error::GITHUB_TOKEN not set", file=sys.stderr)
        return 1
    post(
        repo=args.repo,
        pr_number=args.pr_number,
        body=body,
        token=token,
    )
    return 0


def render(*, downstreams: str, merge_sha: str, run_url: str) -> str:
    """Build the Markdown body of the ack comment."""
    short = merge_sha[:7]
    entries = _render_entries(downstreams)
    return "\n".join(
        [
            "**Downstream validation triggered**",
            "",
            f"Validating this PR (merge ref `{short}`) against: {entries}.",
            "",
            "Each entry runs in LKG mode by default (the PR's commits are"
            " cherry-picked onto the downstream's last-known-good mathlib"
            " commit); entries with `--merge-branch` are tested against the"
            " PR's merge tree instead.  A single follow-up comment with the"
            " full result table will land here when the run finishes.",
            "",
            f"Dispatch: [run]({run_url})",
        ]
    )


def _render_entries(downstreams: str) -> str:
    """Render each entry as a backtick-quoted token, comma-separated.

    Strips any stray backticks from the rev portion before wrapping —
    `git check-ref-format` allows them in refnames, and an unescaped
    backtick mid-token would close the Markdown code span and let
    user-controlled text render unquoted.
    """
    tokens: list[str] = []
    for raw in downstreams.split(","):
        token = raw.strip().replace("`", "")
        if not token:
            continue
        tokens.append(f"`{token}`")
    return ", ".join(tokens)


def post(*, repo: str, pr_number: str, body: str, token: str) -> None:
    """POST *body* as a new comment on ``repo``'s PR ``pr_number``."""
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "leanprover-community/mathlib-ci check-downstream",
        },
        data=json.dumps({"body": body}).encode("utf-8"),
    )
    with urllib.request.urlopen(req, timeout=30) as _resp:
        pass


if __name__ == "__main__":
    sys.exit(main())
