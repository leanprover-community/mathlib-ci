"""Put the composite-action source dir on sys.path, and provide a fake GitHub client."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".github" / "actions" / "validate-lake-manifest"))

from lake_manifest.github import ApiError  # noqa: E402

REPOSITORY = "leanprover-community/mathlib4"
HEAD = "head"
MERGE_BASE = "merge-base"
OLD = "1" * 40
NEW = "2" * 40
FORK = "3" * 40
TAG = "4" * 40


def package(name="batteries", rev=NEW, input_rev="main", url=None, inherited=False, **extra) -> dict:
    url = f"https://github.com/leanprover-community/{name}" if url is None else url
    return {"name": name, "url": url, "inputRev": input_rev, "rev": rev, "inherited": inherited, **extra}


def manifest_text(*packages: dict) -> str:
    return json.dumps({"version": "1.1.0", "packages": list(packages)})


class FakeClient:
    """A `validate.Client` that answers from tables.

    `history` maps a branch or commit to the commits it contains, `tags`
    maps a tag to its commit, and a manifest that is an exception raises.
    `compare_error` makes the mathlib4 compare fail with that status.
    """

    def __init__(self, head, base=None, touched=True, files=None, history=None, tags=None, compare_error=None):
        self.manifests = {HEAD: head, MERGE_BASE: base}
        self.files = files if files is not None else [{"filename": "lake-manifest.json" if touched else "Mathlib.lean"}]
        self.history = {"main": {OLD, NEW}, NEW: {OLD, NEW}, OLD: {OLD}, **(history or {})}
        self.tags = tags or {}
        self.compare_error = compare_error
        self.calls: list[tuple] = []

    def compare(self, repository, base, head):
        self.calls.append(("compare", repository, base, head))
        if self.compare_error:
            raise ApiError("/compare", self.compare_error)
        return {"files": self.files, "merge_base_commit": {"sha": MERGE_BASE}}

    def read_manifest(self, repository, ref):
        self.calls.append(("read_manifest", repository, ref))
        text = self.manifests[ref]
        if isinstance(text, Exception):
            raise text
        return text

    def is_ancestor(self, owner, repo, commit, tip):
        self.calls.append(("is_ancestor", owner, repo, commit, tip))
        return commit in self.history.get(tip, set())

    def tag_commit(self, owner, repo, tag):
        self.calls.append(("tag_commit", owner, repo, tag))
        return self.tags.get(tag)
