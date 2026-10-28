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
P2 = "q" * 40
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


class FakeApi:
    """`route_build.api` backed by a dict of canned responses, recording the calls."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, path, binary=False):
        self.calls.append(path)
        if path not in self.responses:
            raise AssertionError(f"unexpected API call {path}")
        response = self.responses[path]
        if binary:
            return response
        return json.dumps(response)


@pytest.fixture
def fake_api(monkeypatch):
    monkeypatch.setattr(rb, "REPO", REPO)
    monkeypatch.setattr(rb, "HEAD_SHA", HEAD)

    def install(responses):
        api = FakeApi(responses)
        monkeypatch.setattr(rb, "api", api)
        return api

    return install


def path_compare(base):
    return f"repos/{REPO}/compare/{base}...{HEAD}"


def path_runs(sha):
    return f"repos/{REPO}/actions/runs?head_sha={sha}&event=push&branch=master"


def path_artifacts(run):
    return f"repos/{REPO}/actions/runs/{run}/artifacts?name=import-graph"


def path_jobs(run):
    return f"repos/{REPO}/actions/runs/{run}/jobs"


def path_commits(sha):
    return f"repos/{REPO}/commits?sha={sha}&per_page=10"


COMMON = {
    path_compare("master"): compare_response(MB, ["Mathlib/B.lean", "docs/x.md"]),
    path_runs(MB): runs_response((11, "continuous integration"), (12, "other workflow")),
    path_artifacts(11): artifacts_response((GRAPH_URL, False)),
    GRAPH_URL: zipped(),
}


def test_merge_base_build_with_graph(fake_api):
    api = fake_api(COMMON)
    assert rb.predict() == (False, 3, f"3 Mathlib modules to rebuild since the master build {MB}")
    assert api.calls == [path_compare("master"), path_runs(MB), path_artifacts(11), GRAPH_URL]


def test_large_cascade(fake_api, monkeypatch):
    monkeypatch.setattr(rb, "LARGE_MODULES", 3)
    fake_api(COMMON)
    assert rb.predict()[0] is True


def test_renamed_file_counts_both_names(fake_api):
    fake_api({**COMMON, path_compare("master"): compare_response(MB, [], renamed=[("Mathlib/A.lean", "Mathlib/New.lean")])})
    # `New` is not in the graph (1 module); the old name `A` rebuilds `B`, `C` and `D`.
    assert rb.predict()[1] == 5


def test_many_files_pick_the_large_pool_without_a_graph(fake_api):
    api = fake_api({path_compare("master"): compare_response(MB, [f"docs/{i}.md" for i in range(300)])})
    assert rb.predict() == (True, None, "300 or more changed files")
    assert api.calls == [path_compare("master")]


def test_graph_pending_and_merge_base_cached(fake_api):
    """The merge-base's build succeeded but has not uploaded its graph: use the parent's graph, keep the merge-base diff."""
    api = fake_api({
        **COMMON,
        path_artifacts(11): artifacts_response(),
        path_jobs(11): jobs_response("success"),
        path_commits(MB): commits_response(MB, P1, P2),
        path_runs(P1): runs_response((21, "continuous integration")),
        path_artifacts(21): artifacts_response((GRAPH_URL, False)),
    })
    assert rb.predict() == (False, 3, f"3 Mathlib modules to rebuild since the merge-base {MB} (import graph of the master build {P1})")
    assert path_compare(P1) not in api.calls


def test_graph_pending_and_merge_base_building(fake_api):
    """The merge-base's build step has not succeeded: diff from the parent, so master's own changes count."""
    api = fake_api({
        **COMMON,
        path_artifacts(11): artifacts_response(),
        path_jobs(11): jobs_response(None),
        path_commits(MB): commits_response(MB, P1, P2),
        path_runs(P1): runs_response((21, "continuous integration")),
        path_artifacts(21): artifacts_response((GRAPH_URL, False)),
        path_compare(P1): compare_response(P1, ["Mathlib/B.lean", "Mathlib/A.lean"]),
    })
    assert rb.predict() == (False, 4, f"4 Mathlib modules to rebuild since the master build {P1}")
    assert path_compare(P1) in api.calls


def test_merge_base_without_a_build(fake_api):
    """No master build of the merge-base (failed or cancelled run): the parent's graph and diff."""
    fake_api({
        **COMMON,
        path_runs(MB): runs_response(),
        path_commits(MB): commits_response(MB, P1),
        path_runs(P1): runs_response((21, "continuous integration")),
        path_artifacts(21): artifacts_response((GRAPH_URL, False)),
        path_compare(P1): compare_response(P1, ["Mathlib/C.lean"]),
    })
    assert rb.predict() == (False, 1, f"1 Mathlib modules to rebuild since the master build {P1}")


def test_expired_graph_is_skipped(fake_api):
    fake_api({
        **COMMON,
        path_artifacts(11): artifacts_response((GRAPH_URL, True)),
        path_jobs(11): jobs_response("success"),
        path_commits(MB): commits_response(MB, P1),
        path_runs(P1): runs_response((21, "continuous integration")),
        path_artifacts(21): artifacts_response((GRAPH_URL, False)),
    })
    assert rb.predict()[1] == 3


def test_no_graph_at_all(fake_api):
    fake_api({
        **COMMON,
        path_artifacts(11): artifacts_response(),
        path_jobs(11): jobs_response("failure"),
        path_commits(MB): commits_response(MB, P1),
        path_runs(P1): runs_response(),
    })
    assert rb.predict() == (False, None, f"no master build with an import graph at or before the merge-base {MB}")


def run_main(monkeypatch, tmp_path, capsys):
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    monkeypatch.setenv("POOL", "pr")
    monkeypatch.setenv("LARGE_POOL", "pr-large")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    rb.main()
    return capsys.readouterr(), output.read_text(), summary.read_text()


def test_main_writes_the_verdict(fake_api, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(rb, "LARGE_MODULES", 3)
    fake_api(COMMON)
    captured, output, summary = run_main(monkeypatch, tmp_path, capsys)
    assert output == "pool=pr-large\nlarge=true\nmodules=3\n"
    assert summary == f"Runner pool: `pr-large` (large: true; 3 Mathlib modules to rebuild since the master build {MB})\n"
    assert f"::notice title=Runner pool::pr-large (large: true; 3 Mathlib modules" in captured.out


def test_main_picks_the_pool_on_an_error(fake_api, monkeypatch, tmp_path, capsys):
    fake_api({})  # every API call fails
    captured, output, summary = run_main(monkeypatch, tmp_path, capsys)
    assert output == "pool=pr\nlarge=false\nmodules=\n"
    assert "no prediction" in captured.out
    assert "unexpected API call" in captured.err
