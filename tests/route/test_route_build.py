"""Tests for `scripts/route/route_build.py`, with the GitHub API replaced by canned responses."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

import route_build as rb

REPO = "owner/repo"
HEAD = "h" * 40
MB = "m" * 40  # the merge-base
P1 = "p" * 40  # its parent
GRAPH_URL = "https://api.github.com/repos/owner/repo/actions/artifacts/1/zip"

# `B` imports `A`; `C` and `D` import `B`.
DOT = """digraph "import_graph" {
  "Mathlib.A" -> "Mathlib.B";
  "Mathlib.B" -> "Mathlib.C";
  "Mathlib.B" -> "Mathlib.D";
}
"""


def zipped():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("import_graph.dot", DOT)
    return buf.getvalue()


def compare(paths, renamed=()):
    files = [{"filename": p} for p in paths] + [{"filename": new, "previous_filename": old} for old, new in renamed]
    return {"merge_base_commit": {"sha": MB}, "files": files}


def runs(run_id):
    return {"workflow_runs": [{"id": run_id, "name": "continuous integration"}]}


def artifacts(*urls):
    return {"artifacts": [{"archive_download_url": url, "expired": False} for url in urls]}


def jobs(conclusion):
    return {"jobs": [{"name": "ci / Build", "conclusion": conclusion}]}


# The merge-base's master build (run 11) has uploaded its graph.
GRAPH_AT_MERGE_BASE = {
    f"repos/{REPO}/compare/master...{HEAD}": compare(["Mathlib/B.lean", "docs/x.md"]),
    f"repos/{REPO}/actions/runs?head_sha={MB}&event=push&branch=master": runs(11),
    f"repos/{REPO}/actions/runs/11/artifacts?name=import-graph-dot": artifacts(GRAPH_URL),
    GRAPH_URL: zipped(),
}

# The merge-base's build has no graph yet; its parent's build (run 21) has one.
GRAPH_AT_PARENT = {
    **GRAPH_AT_MERGE_BASE,
    f"repos/{REPO}/actions/runs/11/artifacts?name=import-graph-dot": artifacts(),
    f"repos/{REPO}/commits?sha={MB}&per_page={rb.ANCESTORS + 1}": [{"sha": MB}, {"sha": P1}],
    f"repos/{REPO}/actions/runs?head_sha={P1}&event=push&branch=master": runs(21),
    f"repos/{REPO}/actions/runs/21/artifacts?name=import-graph-dot": artifacts(GRAPH_URL),
}


@pytest.fixture
def responses(monkeypatch):
    """The canned API responses; any other call is an error."""
    canned = {}

    def api(path, binary=False):
        if path not in canned:
            raise AssertionError(f"unexpected API call {path}")
        return canned[path] if binary else json.dumps(canned[path])

    monkeypatch.setattr(rb, "REPO", REPO)
    monkeypatch.setattr(rb, "HEAD_SHA", HEAD)
    monkeypatch.setattr(rb, "api", api)
    return canned


def test_cascade_counts_changed_modules_and_transitive_importers():
    dependents = rb.read_graph(DOT)
    assert rb.cascade(dependents, ["Mathlib/A.lean"]) == 4
    assert rb.cascade(dependents, ["Mathlib/B.lean", "Mathlib/C.lean"]) == 3
    assert rb.cascade(dependents, ["Mathlib/New.lean", "Mathlib.lean", "docs/x.md"]) == 1


def test_graph_of_the_merge_base_build(responses):
    responses.update(GRAPH_AT_MERGE_BASE)
    assert rb.predict() == (False, 3, f"3 Mathlib modules to rebuild since {MB} (import graph of {MB})")


def test_a_renamed_file_counts_both_names(responses):
    responses.update(GRAPH_AT_MERGE_BASE)
    responses[f"repos/{REPO}/compare/master...{HEAD}"] = compare([], renamed=[("Mathlib/A.lean", "Mathlib/New.lean")])
    assert rb.predict()[1] == 5


@pytest.mark.parametrize(
    "paths, why",
    [
        (["lean-toolchain", "Mathlib/A.lean"], "lean-toolchain changed, so every module rebuilds"),
        ([f"docs/{i}.md" for i in range(300)], "300 or more changed files"),
    ],
)
def test_large_without_a_graph(responses, paths, why):
    responses[f"repos/{REPO}/compare/master...{HEAD}"] = compare(paths)
    assert rb.predict() == (True, None, why)


def test_graph_pending_and_merge_base_cached(responses):
    """The merge-base's build step succeeded, so the diff starts at the merge-base."""
    responses.update(GRAPH_AT_PARENT)
    responses[f"repos/{REPO}/actions/runs/11/jobs"] = jobs("success")
    assert rb.predict() == (False, 3, f"3 Mathlib modules to rebuild since {MB} (import graph of {P1})")


def test_graph_pending_and_merge_base_building(responses):
    """The merge-base's build step has not succeeded, so the diff from the parent counts master's change to `A`."""
    responses.update(GRAPH_AT_PARENT)
    responses[f"repos/{REPO}/actions/runs/11/jobs"] = jobs(None)
    responses[f"repos/{REPO}/compare/{P1}...{HEAD}"] = compare(["Mathlib/B.lean", "Mathlib/A.lean"])
    assert rb.predict() == (False, 4, f"4 Mathlib modules to rebuild since {P1} (import graph of {P1})")


def test_no_graph_at_all(responses):
    responses.update(GRAPH_AT_PARENT)
    responses[f"repos/{REPO}/actions/runs/21/artifacts?name=import-graph-dot"] = artifacts()
    assert rb.predict() == (False, None, f"no master build with an import graph at or before the merge-base {MB}")


@pytest.mark.parametrize("graph, output", [(True, "large=false\nmodules=3\n"), (False, "large=false\nmodules=\n")])
def test_main_writes_the_verdict(responses, monkeypatch, tmp_path, graph, output):
    if graph:
        responses.update(GRAPH_AT_MERGE_BASE)  # otherwise every API call fails
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    rb.main()
    assert (tmp_path / "output").read_text() == output
