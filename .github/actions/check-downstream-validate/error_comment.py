"""POST a user-facing comment when a `!downstream-check` directive is malformed.

The comment quotes the parser's error + an optional hint, leads with
an @-mention of the commenter so they get a notification, and includes
a usage reminder + link to the design doc.  Runtime errors (unknown
downstream, build failures, etc.) are not handled here — those live on
the dispatched workflow's side so the two repos stay decoupled.
"""

from __future__ import annotations

import json
import os
import urllib.request

from grammar import GrammarError

# Where the canonical grammar lives.  The action calls into mathlib-ci's
# territory but the directive's design doc lives in downstream-reports;
# linking there gives the requester the full reference rather than just
# the one-line error.
_DOCS_URL = (
    "https://github.com/leanprover-community/downstream-reports/"
    "blob/main/docs/internal/pr-validation-workflow.md"
)


def render(*, commenter: str, error: GrammarError) -> str:
    """Build the Markdown body of the error comment."""
    mention = f"Hi @{commenter}," if commenter else "Hi,"
    lines: list[str] = [
        f"{mention} I couldn't validate your `!downstream-check` directive:",
        "",
        f"> {error.message}",
    ]
    if error.hint:
        lines.append(f"> ({error.hint})")
    lines.extend(
        [
            "",
            "The directive grammar is:",
            "",
            "    !downstream-check <name-or-slug>[@<rev>]"
            " [--merge-branch][, ...]",
            "",
            f"See [the design doc]({_DOCS_URL}) for details.",
        ]
    )
    return "\n".join(lines)


def post(*, repo: str, pr_number: str, body: str, token: str) -> None:
    """POST *body* as a new comment on ``repo``'s PR ``pr_number``.

    Always POSTs a fresh comment (no edit-in-place) so retries of a
    bad directive leave a visible audit trail and the user sees each
    correction attempt separately.
    """
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


def post_grammar_error(
    *,
    repo: str,
    pr_number: str,
    commenter: str,
    error: GrammarError,
    token: str | None = None,
) -> None:
    """Convenience: render then POST in one call.

    *token* defaults to ``$GITHUB_TOKEN`` so the workflow YAML doesn't
    have to pass it through twice (action input + Python flag).
    """
    body = render(commenter=commenter, error=error)
    auth = token or os.environ.get("GITHUB_TOKEN", "")
    if not auth:
        # Fail loudly rather than silently swallow the comment; the
        # composite action's `env: GITHUB_TOKEN:` is required.
        raise RuntimeError(
            "GITHUB_TOKEN not set; cannot post grammar-error comment"
        )
    post(repo=repo, pr_number=pr_number, body=body, token=auth)
