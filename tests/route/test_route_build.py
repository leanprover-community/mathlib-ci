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


def zipped(dot=DOT):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("import_graph.dot", dot)
        z.writestr("decls.txt", "irrelevant\n")
    return buf.getvalue()


def compare_response(merge_base, paths, renamed=()):
    files = [{"filename": p} for p in paths] + [{"filename": new, "previous_filename": old} for old, new in renamed]
    return {"merge_base_commit": {"sha": merge_base}, "files": files}


def runs_response(*runs):
    return {"workflow_runs": [{"id": run_id, "name": name} for run_id, name in runs]}


def artifacts_response(*artifacts):
    return {"artifacts": [{"archive_download_url": url, "expired": expired} for url, expired in artifacts]}


def jobs_response(build_conclusion):
    return {"jobs": [{"name": "ci / Lint style", "conclusion": "success"}, {"name": "ci / Build", "conclusion": build_conclusion}]}


def commits_response(*shas):
    return [{"sha": sha} for sha in shas]


def fake_api(responses):
    """`route_build.api` backed by canned responses; any other call is an error."""

    def api(path, binary=False):
        if path not in responses:
            raise AssertionError(f"unexpected API call {path}")
        return responses[path] if binary else json.dumps(responses[path])

    return api


@pytest.fixture
def responses(monkeypatch):
    """Installs the canned responses in `route_build`; tests update the dict before calling."""
    monkeypatch.setattr(rb, "REPO", REPO)
    monkeypatch.setattr(rb, "HEAD_SHA", HEAD)
    canned = {}
    monkeypatch.setattr(rb, "api", fake_api(canned))
    return canned


def path_compare(base):
    return f"repos/{REPO}/compare/{base}...{HEAD}"


def path_runs(sha):
    return f"repos/{REPO}/actions/runs?head_sha={sha}&event=push&branch=master"


def path_artifacts(run):
    return f"repos/{REPO}/actions/runs/{run}/artifacts?name=import-graph-dot"


def path_jobs(run):
    return f"repos/{REPO}/actions/runs/{run}/jobs"


def path_commits(sha):
    return f"repos/{REPO}/commits?sha={sha}&per_page=10"


# The merge-base's master build has uploaded its graph; the change touches `B`.
GRAPH_AT_MERGE_BASE = {
    path_compare("master"): compare_response(MB, ["Mathlib/B.lean", "docs/x.md"]),
    path_runs(MB): runs_response((11, "continuous integration"), (12, "other workflow")),
    path_artifacts(11): artifacts_response((GRAPH_URL, False)),
    GRAPH_URL: zipped(),
}

# The merge-base's master build has no graph; its parent's build has one.
GRAPH_AT_PARENT = {
    **GRAPH_AT_MERGE_BASE,
    path_artifacts(11): artifacts_response(),
    path_commits(MB): commits_response(MB, P1),
    path_runs(P1): runs_response((21, "continuous integration")),
    path_artifacts(21): artifacts_response((GRAPH_URL, False)),
}


def test_graph_of_the_merge_base_build(responses):
    responses.update(GRAPH_AT_MERGE_BASE)
    assert rb.predict() == (False, 3, f"3 Mathlib modules to rebuild since the master build {MB}")


def test_large_at_the_threshold(responses, monkeypatch):
    monkeypatch.setattr(rb, "LARGE_MODULES", 3)
    responses.update(GRAPH_AT_MERGE_BASE)
    assert rb.predict()[0] is True


def test_a_renamed_file_counts_both_names(responses):
    responses.update(GRAPH_AT_MERGE_BASE)
    responses[path_compare("master")] = compare_response(MB, [], renamed=[("Mathlib/A.lean", "Mathlib/New.lean")])
    # `New` is not in the graph (1 module); the old name `A` rebuilds `B`, `C` and `D`.
    assert rb.predict()[1] == 5


def test_300_files_are_large_without_a_graph(responses):
    responses[path_compare("master")] = compare_response(MB, [f"docs/{i}.md" for i in range(300)])
    assert rb.predict() == (True, None, "300 or more changed files")


def test_graph_pending_and_merge_base_cached(responses):
    """The merge-base's build step succeeded: its parent's graph with the merge-base diff."""
    responses.update(GRAPH_AT_PARENT)
    responses[path_jobs(11)] = jobs_response("success")
    assert rb.predict() == (False, 3, f"3 Mathlib modules to rebuild since the merge-base {MB} (import graph of the master build {P1})")


def test_graph_pending_and_merge_base_building(responses):
    """The merge-base's build step has not succeeded: the diff from the parent counts master's own change to `A`."""
    responses.update(GRAPH_AT_PARENT)
    responses[path_jobs(11)] = jobs_response(None)
    responses[path_compare(P1)] = compare_response(P1, ["Mathlib/B.lean", "Mathlib/A.lean"])
    assert rb.predict() == (False, 4, f"4 Mathlib modules to rebuild since the master build {P1}")


def test_merge_base_without_a_master_build(responses):
    responses.update(GRAPH_AT_PARENT)
    responses[path_runs(MB)] = runs_response()
    responses[path_compare(P1)] = compare_response(P1, ["Mathlib/C.lean"])
    assert rb.predict() == (False, 1, f"1 Mathlib modules to rebuild since the master build {P1}")


def test_an_expired_graph_does_not_count(responses):
    responses.update(GRAPH_AT_PARENT)
    responses[path_artifacts(11)] = artifacts_response((GRAPH_URL, True))
    responses[path_jobs(11)] = jobs_response("success")
    assert rb.predict()[2].endswith(f"(import graph of the master build {P1})")


def test_no_graph_at_all(responses):
    responses.update(GRAPH_AT_PARENT)
    responses[path_jobs(11)] = jobs_response("failure")
    responses[path_runs(P1)] = runs_response()
    assert rb.predict() == (False, None, f"no master build with an import graph at or before the merge-base {MB}")


def run_main(monkeypatch, tmp_path, capsys):
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    rb.main()
    return capsys.readouterr(), output.read_text(), summary.read_text()


def test_main_writes_the_verdict(responses, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(rb, "LARGE_MODULES", 3)
    responses.update(GRAPH_AT_MERGE_BASE)
    captured, output, summary = run_main(monkeypatch, tmp_path, capsys)
    assert output == "large=true\nmodules=3\n"
    assert summary == f"Large build: true (3 Mathlib modules to rebuild since the master build {MB})\n"
    assert "::notice title=Large build::true (3 Mathlib modules" in captured.out


def test_main_gives_large_false_on_an_error(responses, monkeypatch, tmp_path, capsys):
    captured, output, summary = run_main(monkeypatch, tmp_path, capsys)  # every API call fails
    assert output == "large=false\nmodules=\n"
    assert "no prediction" in captured.out
    assert "Traceback" in captured.err
