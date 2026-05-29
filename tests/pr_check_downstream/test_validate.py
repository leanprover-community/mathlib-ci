"""Tests for validate.main and _resolve_merge_sha (all HTTP calls are patched)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import validate


def _fake_pr_response(*, sha: str | None, mergeable: bool | None) -> MagicMock:
    """Build a fake urllib response for the PR API."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(
        {"merge_commit_sha": sha, "mergeable": mergeable}
    ).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=None)
    return resp


def _run_main(output: Path, names: str) -> int:
    """Invoke validate.main with fixed PR/repo/commenter args, varying only `names`."""
    return validate.main(
        [
            "--names", names,
            "--pr-number", "39315",
            "--repo", "leanprover-community/mathlib4",
            "--commenter", "marcelolynch",
            "--output", str(output),
        ]
    )


class TestMain:
    def test_valid_directive_writes_outputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A well-formed directive writes resolved_names + merge_sha and exits 0."""
        output = tmp_path / "gh-output"
        output.write_text("")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        sha = "299461184e256b5f1c8b940b830ca1fce7377aee"
        with patch(
            "validate.urllib.request.urlopen",
            return_value=_fake_pr_response(sha=sha, mergeable=True),
        ):
            rc = _run_main(output, "FLT, Toric --merge-branch")

        assert rc == 0
        out_lines = output.read_text().splitlines()
        assert "resolved_names=FLT,Toric --merge-branch" in out_lines
        assert f"merge_sha={sha}" in out_lines

    def test_grammar_error_posts_comment_and_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An invalid directive POSTs an error comment, writes nothing, and returns 1."""
        output = tmp_path / "gh-output"
        output.write_text("")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        with patch("validate.post_grammar_error") as post_mock:
            rc = _run_main(output, "FLT --bogus")

        assert rc == 1
        call_kwargs = post_mock.call_args.kwargs
        assert call_kwargs["repo"] == "leanprover-community/mathlib4"
        assert call_kwargs["pr_number"] == "39315"
        assert call_kwargs["commenter"] == "marcelolynch"
        assert "--bogus" in call_kwargs["error"].message
        assert output.read_text() == ""  # no outputs written on failure


class TestResolveMergeSha:
    def test_mergeable_false_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A conflicting PR is a runtime issue with the PR, not the directive —
        fail the workflow rather than POST a misleading grammar-error comment."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        with patch(
            "validate.urllib.request.urlopen",
            return_value=_fake_pr_response(sha=None, mergeable=False),
        ):
            with pytest.raises(SystemExit):
                validate._resolve_merge_sha("owner/repo", "1")

    def test_no_merge_commit_sha_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Null `merge_commit_sha` means GitHub hasn't computed mergeability yet."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        with patch(
            "validate.urllib.request.urlopen",
            return_value=_fake_pr_response(sha=None, mergeable=True),
        ):
            with pytest.raises(SystemExit):
                validate._resolve_merge_sha("owner/repo", "1")

    def test_returns_sha_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A mergeable PR with a resolved SHA returns it verbatim."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        sha = "299461184e256b5f1c8b940b830ca1fce7377aee"
        with patch(
            "validate.urllib.request.urlopen",
            return_value=_fake_pr_response(sha=sha, mergeable=True),
        ):
            assert validate._resolve_merge_sha("owner/repo", "1") == sha

    def test_missing_token_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Caught at the boundary so we don't fire an unauth, rate-limited PR lookup."""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        with pytest.raises(SystemExit):
            validate._resolve_merge_sha("owner/repo", "1")
