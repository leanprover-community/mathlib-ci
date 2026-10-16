"""Put the script's source directory on sys.path, and provide git repo helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "dependencies"

sys.path.insert(0, str(_SCRIPT_DIR))

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


class Repo:
    """A throwaway git repository in which each commit gets a given subject."""

    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.n = 0

    def git(self, *args: str) -> str:
        env = os.environ.copy()
        env.update(_GIT_ENV)
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        return proc.stdout.strip()

    def commit(self, subject: str) -> str:
        self.n += 1
        self.git("commit", "-q", "--allow-empty", "-m", subject)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def make_repo(tmp_path):
    def make(name: str) -> Repo:
        return Repo(tmp_path / ".lake" / "packages" / name)

    return make


@pytest.fixture
def write_manifests(tmp_path):
    """Write an old and a new manifest; return their paths.

    Each argument maps a package name to `(url, rev)`.
    """

    def write(old: dict, new: dict) -> tuple[Path, Path]:
        def doc(pkgs):
            return {
                "version": "1.2.0",
                "packagesDir": ".lake/packages",
                "packages": [
                    {"type": "git", "name": name, "url": url, "rev": rev}
                    for name, (url, rev) in pkgs.items()
                ],
            }

        old_path = tmp_path / "old-manifest.json"
        new_path = tmp_path / "lake-manifest.json"
        old_path.write_text(json.dumps(doc(old)))
        new_path.write_text(json.dumps(doc(new)))
        return old_path, new_path

    return write
