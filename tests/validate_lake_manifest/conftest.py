"""Put the composite-action source dir on sys.path, and provide a fake GitHub client."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".github" / "actions" / "validate-lake-manifest"))

REPOSITORY = "leanprover-community/mathlib4"
HEAD = "head"
MERGE_BASE = "merge-base"
OLD = "1" * 40
NEW = "2" * 40
FORK = "3" * 40
TAG = "4" * 40


def package(name="batteries", rev=NEW, input_rev="main", url=None, inherited=False) -> dict:
    url = f"https://github.com/leanprover-community/{name}" if url is None else url
    return {"name": name, "url": url, "inputRev": input_rev, "rev": rev, "inherited": inherited}


def manifest_text(*packages: dict) -> str:
    return json.dumps({"version": "1.1.0", "packages": list(packages)})


class FakeGitHub:
    """Answers from tables. `main` holds OLD and NEW; FORK and TAG are on no branch.

    A manifest that is an exception raises.
    """

    HISTORY = {"main": {OLD, NEW}, NEW: {OLD, NEW}, OLD: {OLD}}

    def __init__(self, head, base=None, tags=None):
        self.manifests = {HEAD: head, MERGE_BASE: base}
        self.tags = tags or {}

    def read_manifest(self, repository, ref):
        text = self.manifests[ref]
        if isinstance(text, Exception):
            raise text
        return text

    def merge_base(self, repository, base, head):
        return MERGE_BASE

    def is_ancestor(self, repository, commit, tip):
        return commit in self.HISTORY.get(tip, set())

    def tag_commit(self, repository, tag):
        return self.tags.get(tag)
