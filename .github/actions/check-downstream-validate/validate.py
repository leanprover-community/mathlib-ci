"""Entry point for the ``check-downstream-validate`` composite action.

Reads the directive entries, parses them, resolves the PR's
``merge_commit_sha`` via the GitHub API, and emits ``resolved_names``
+ ``merge_sha`` as step outputs.

On grammar errors, posts a one-shot comment to the PR explaining what
went wrong + the directive grammar, then exits non-zero so the calling
workflow stops at this step rather than dispatching a doomed run.

Runtime errors (unknown downstream, build failures, etc.) are not in
scope here — the dispatched ``downstream-reports`` workflow owns its
own reporting for those, keeping the two repos decoupled.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from error_comment import post_grammar_error
from grammar import GrammarError, parse_directive, serialize


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--names",
        required=True,
        help="Comma-separated entries (with `!downstream-check ` prefix already stripped).",
    )
    p.add_argument(
        "--pr-number",
        required=True,
        help="PR number on --repo.",
    )
    p.add_argument(
        "--repo",
        required=True,
        help="`owner/repo` of the PR (used for both the PR API lookup and the error-comment POST).",
    )
    p.add_argument(
        "--commenter",
        default="",
        help="Login of the user who triggered the directive (for the @-mention in error comments).",
    )
    p.add_argument(
        "--output",
        required=True,
        help="Path to write KEY=VALUE outputs (pass $GITHUB_OUTPUT).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        entries = parse_directive(args.names)
    except GrammarError as err:
        # The PR author sees this comment.  We also leave an ::error::
        # annotation in the workflow log so a maintainer scrolling the
        # run UI can see the same failure without clicking through.
        post_grammar_error(
            repo=args.repo,
            pr_number=args.pr_number,
            commenter=args.commenter,
            error=err,
        )
        print(f"::error::{err.message}", file=sys.stderr)
        if err.hint:
            print(f"::error::{err.hint}", file=sys.stderr)
        return 1

    merge_sha = _resolve_merge_sha(args.repo, args.pr_number)
    resolved = ",".join(serialize(e) for e in entries)

    with open(args.output, "a", encoding="utf-8") as fh:
        fh.write(f"resolved_names={resolved}\n")
        fh.write(f"merge_sha={merge_sha}\n")
    print(f"validated entries: {resolved}")
    print(f"merge: {merge_sha}")
    return 0


def _resolve_merge_sha(repo: str, pr_number: str) -> str:
    """Fetch ``merge_commit_sha`` from the PR API.

    ``mergeable=false`` (computed conflict) or ``merge_commit_sha=null``
    (GitHub still computing) both fail the workflow loudly — the
    caller can retry once GitHub finishes the merge computation.  This
    is a hard error, not a grammar error: no PR comment, just a fast
    exit with an ``::error::`` annotation.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("::error::GITHUB_TOKEN not set", file=sys.stderr)
        raise SystemExit(1)
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "leanprover-community/mathlib-ci check-downstream",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        print(
            f"::error::PR API lookup failed for {repo}#{pr_number}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    if data.get("mergeable") is False:
        print(
            f"::error::PR {pr_number} reports mergeable=false (conflicts)",
            file=sys.stderr,
        )
        raise SystemExit(1)
    sha = data.get("merge_commit_sha")
    if not sha:
        print(
            f"::error::PR {pr_number} has no merge_commit_sha"
            " (GitHub may still be computing mergeability — retry shortly)",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return sha


if __name__ == "__main__":
    sys.exit(main())
