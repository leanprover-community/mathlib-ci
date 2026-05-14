"""
Tests for: validate.main (the composite-action entry point)

Coverage scope:
    - On a valid directive, main() writes `resolved_names` + `merge_sha`
      to the GITHUB_OUTPUT file and returns 0.
    - On a grammar error, main() POSTs a user-facing comment to the
      PR and returns 1 (with the `error_comment.post` side effect
      stubbed out).
    - `_resolve_merge_sha` raises SystemExit when the PR has merge
      conflicts or no merge_commit_sha yet.

Out of scope:
    - Real GitHub API calls — all `urllib.request.urlopen` /
      `error_comment.post_grammar_error` calls are patched out.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import validate
from grammar import MODE_LKG, MODE_MERGE


def _fake_pr_response(*, sha: str | None, mergeable: bool | None) -> MagicMock:
    """Build a fake urllib response for the PR API."""
    payload = {"merge_commit_sha": sha, "mergeable": mergeable}
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=None)
    return resp


class TestMain:
    """End-to-end exercise of `validate.main` with the HTTP layer stubbed out."""

    def test_valid_directive_writes_outputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A well-formed directive writes resolved_names + merge_sha and exits 0."""
        # Arrange
        output = tmp_path / "gh-output"
        output.write_text("")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")

        sha = "299461184e256b5f1c8b940b830ca1fce7377aee"
        with patch(
            "validate.urllib.request.urlopen",
            return_value=_fake_pr_response(sha=sha, mergeable=True),
        ):
            # Act
            rc = validate.main(
                [
                    "--names",
                    "FLT, Toric --merge-branch",
                    "--pr-number",
                    "39315",
                    "--repo",
                    "leanprover-community/mathlib4",
                    "--commenter",
                    "marcelolynch",
                    "--output",
                    str(output),
                ]
            )

        # Assert
        assert rc == 0
        out_lines = output.read_text().splitlines()
        # Resolved names round-tripped through serialize()
        assert "resolved_names=FLT,Toric --merge-branch" in out_lines
        assert f"merge_sha={sha}" in out_lines

    def test_grammar_error_posts_comment_and_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An invalid directive POSTs an error comment and returns 1."""
        # Arrange
        output = tmp_path / "gh-output"
        output.write_text("")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")

        with patch("validate.post_grammar_error") as post_mock:
            # Act
            rc = validate.main(
                [
                    "--names",
                    "FLT --bogus",
                    "--pr-number",
                    "39315",
                    "--repo",
                    "leanprover-community/mathlib4",
                    "--commenter",
                    "marcelolynch",
                    "--output",
                    str(output),
                ]
            )

        # Assert
        assert rc == 1
        # The error-comment poster was called with the parser's error.
        assert post_mock.called
        call_kwargs = post_mock.call_args.kwargs
        assert call_kwargs["repo"] == "leanprover-community/mathlib4"
        assert call_kwargs["pr_number"] == "39315"
        assert call_kwargs["commenter"] == "marcelolynch"
        assert "--bogus" in call_kwargs["error"].message
        # And no outputs written.
        assert output.read_text() == ""

    def test_empty_directive_posts_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty directive body POSTs the usage hint and returns 1.

        Catches the case where the user types `!downstream-check`
        with no arguments — they get told what the grammar is
        instead of a silent workflow failure.
        """
        # Arrange
        output = tmp_path / "gh-output"
        output.write_text("")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")

        with patch("validate.post_grammar_error") as post_mock:
            # Act
            rc = validate.main(
                [
                    "--names",
                    "",
                    "--pr-number",
                    "39315",
                    "--repo",
                    "leanprover-community/mathlib4",
                    "--commenter",
                    "marcelolynch",
                    "--output",
                    str(output),
                ]
            )

        # Assert
        assert rc == 1
        assert post_mock.called
        err = post_mock.call_args.kwargs["error"]
        assert "no downstream entries" in err.message
        assert "!downstream-check" in err.hint


class TestResolveMergeSha:
    """`_resolve_merge_sha` fails loudly on un-mergeable PRs."""

    def test_mergeable_false_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A PR with `mergeable=false` raises SystemExit (not a grammar error).

        Conflicting PRs are a runtime issue with the PR itself, not
        the directive — fail the workflow rather than POST an error
        comment that would be misleading.
        """
        # Arrange
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        with patch(
            "validate.urllib.request.urlopen",
            return_value=_fake_pr_response(sha=None, mergeable=False),
        ):
            # Act / Assert
            with pytest.raises(SystemExit):
                validate._resolve_merge_sha("owner/repo", "1")

    def test_no_merge_commit_sha_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A PR with `merge_commit_sha=null` raises (mergeability not yet computed)."""
        # Arrange
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        with patch(
            "validate.urllib.request.urlopen",
            return_value=_fake_pr_response(sha=None, mergeable=True),
        ):
            # Act / Assert
            with pytest.raises(SystemExit):
                validate._resolve_merge_sha("owner/repo", "1")

    def test_returns_sha_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A mergeable PR with a resolved SHA returns the SHA verbatim."""
        # Arrange
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        sha = "299461184e256b5f1c8b940b830ca1fce7377aee"
        with patch(
            "validate.urllib.request.urlopen",
            return_value=_fake_pr_response(sha=sha, mergeable=True),
        ):
            # Act
            got = validate._resolve_merge_sha("owner/repo", "1")

        # Assert
        assert got == sha

    def test_missing_token_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No GITHUB_TOKEN in env raises SystemExit before any HTTP call.

        Caught at the boundary so we don't silently try an unauth
        request to the PR API and get rate-limited.
        """
        # Arrange
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        # Act / Assert
        with pytest.raises(SystemExit):
            validate._resolve_merge_sha("owner/repo", "1")
