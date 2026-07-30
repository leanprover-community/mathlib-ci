"""End-to-end tests for verify_commits.sh (the worker).

Each test builds a throwaway git repo, lays down commits with the relevant
subject prefixes, runs the real script with `--json`, and inspects the parsed
result. Covers the transient/auto verification paths and the two runtime guards
(MAX_AUTO_COMMITS, MAX_TRANSIENT_REPLAY_COMMITS) added alongside these tests.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

WORKER = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "verification"
    / "verify_commits.sh"
)

# Isolate git from the user's global/system config (gpg signing, hooks, aliases)
# and pin a deterministic identity.
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _env(extra=None):
    e = os.environ.copy()
    e.update(GIT_ENV)
    if extra:
        e.update({k: str(v) for k, v in extra.items()})
    return e


class Repo:
    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self._git("init", "-q", "-b", "main")
        self._git("config", "commit.gpgsign", "false")
        # Base commit; used as the base_ref for verification.
        self.base = self.commit("base commit", {"README.md": "base\n"})

    def _git(self, *args) -> str:
        r = subprocess.run(
            ["git", *args], cwd=self.path, env=_env(), capture_output=True, text=True
        )
        assert r.returncode == 0, f"git {args} failed:\n{r.stderr}"
        return r.stdout.strip()

    def commit(self, subject: str, files: dict | None = None) -> str:
        """Apply `files` ({path: content}, content None ⇒ delete) and commit."""
        for name, content in (files or {}).items():
            p = self.path / name
            if content is None:
                if p.exists():
                    p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", subject)
        return self._git("rev-parse", "HEAD")

    def run(self, *, base: str | None = None, env_extra=None):
        """Run the worker in --json mode; return (parsed_json, exit_code)."""
        r = subprocess.run(
            ["bash", str(WORKER), base or self.base, "--json"],
            cwd=self.path,
            env=_env(env_extra),
            capture_output=True,
            text=True,
        )
        # 0 = all passed, 1 = verification failed; both emit JSON on stdout.
        assert r.returncode in (0, 1), f"worker crashed ({r.returncode}):\n{r.stderr}"
        return json.loads(r.stdout), r.returncode


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path / "repo")


# --- categorization / no special commits -----------------------------------

def test_no_special_commits_passes(repo):
    repo.commit("feat: a normal change", {"a.txt": "1\n"})
    result, code = repo.run()
    assert code == 0
    assert result["success"] is True
    assert len(result["substantive_commits"]) == 1
    assert result["auto_commits"] == []
    assert result["transient_commits"] == []


# --- automated commits ------------------------------------------------------

def test_auto_commit_that_reproduces_is_verified(repo):
    # Command run at the parent regenerates exactly what the commit introduced.
    repo.commit("x: printf 'hello\\n' > gen.txt", {"gen.txt": "hello\n"})
    result, code = repo.run()
    assert code == 0
    assert result["success"] is True
    assert len(result["auto_commits"]) == 1
    assert result["auto_commits"][0]["verified"] is True


def test_auto_commit_tree_mismatch(repo):
    # Command produces different content than the commit recorded.
    repo.commit("x: printf 'actual\\n' > gen.txt", {"gen.txt": "committed\n"})
    result, code = repo.run()
    assert code == 1
    assert result["success"] is False
    ac = result["auto_commits"][0]
    assert ac["verified"] is False
    assert ac["failure_kind"] == "tree_mismatch"


def test_auto_command_failure(repo):
    repo.commit("x: exit 3", {"marker.txt": "x\n"})
    result, code = repo.run()
    assert code == 1
    ac = result["auto_commits"][0]
    assert ac["verified"] is False
    assert ac["failure_kind"] == "command_failed"
    assert ac["exit_code"] == 3


def test_auto_command_timeout(repo):
    repo.commit("x: sleep 5", {"marker.txt": "x\n"})
    result, code = repo.run(env_extra={"TIMEOUT_SECONDS": 1})
    assert code == 1
    ac = result["auto_commits"][0]
    assert ac["failure_kind"] == "timed_out"


def test_legacy_space_prefix_recognized(repo):
    repo.commit("x printf 'hi\\n' > gen.txt", {"gen.txt": "hi\n"})
    result, code = repo.run()
    assert code == 0
    assert len(result["auto_commits"]) == 1
    assert result["auto_commits"][0]["verified"] is True


# --- transient commits ------------------------------------------------------

def test_transient_no_op_verified(repo):
    # Two transient commits that cancel: net effect on the tree is nothing.
    repo.commit("transient: create x", {"x.txt": "1\n"})
    repo.commit("transient: remove x", {"x.txt": None})
    result, code = repo.run()
    assert code == 0
    assert result["success"] is True
    assert result["transient_verified"] is True
    assert len(result["transient_commits"]) == 2


def test_transient_nonempty_effect_fails(repo):
    repo.commit("transient: leftover", {"x.txt": "1\n"})
    result, code = repo.run()
    assert code == 1
    assert result["transient_verified"] is False
    assert result["transient_failure_kind"] == "tree_mismatch"


def test_transient_verified_with_nontransient_replay(repo):
    # Exercises the cherry-pick path: a real (non-transient) commit is replayed
    # onto the merge base, and the cancelling transient pair nets to nothing.
    repo.commit("feat: add b", {"b.txt": "1\n"})
    repo.commit("transient: create x", {"x.txt": "1\n"})
    repo.commit("transient: remove x", {"x.txt": None})
    result, code = repo.run()
    assert code == 0
    assert result["transient_verified"] is True
    assert len(result["substantive_commits"]) == 1
    assert len(result["transient_commits"]) == 2


# --- runtime guards ---------------------------------------------------------

def test_auto_commit_limit_exceeded_runs_nothing(repo):
    for i in range(3):
        repo.commit(f"x: printf '{i}\\n' > g{i}.txt", {f"g{i}.txt": f"{i}\n"})
    result, code = repo.run(env_extra={"MAX_AUTO_COMMITS": 2})
    assert code == 1
    assert result["success"] is False
    assert result["auto_limit_exceeded"] is True
    assert result["auto_limit"] == 2
    assert len(result["auto_commits"]) == 3
    assert all(c["verified"] is False for c in result["auto_commits"])
    assert all(c["failure_kind"] == "skipped_limit" for c in result["auto_commits"])


def test_transient_replay_limit_exceeded_skips(repo):
    for i in range(3):
        repo.commit(f"feat: change {i}", {f"f{i}.txt": f"{i}\n"})
    repo.commit("transient: temp", {"t.txt": "1\n"})
    result, code = repo.run(env_extra={"MAX_TRANSIENT_REPLAY_COMMITS": 2})
    assert code == 1
    assert result["transient_verified"] is False
    assert result["transient_failure_kind"] == "range_too_large"
    assert result["transient_replay_count"] == 3
    assert result["transient_replay_limit"] == 2
