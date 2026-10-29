#!/usr/bin/env python3
"""
Usage: route_build.py

Predicts whether a build of mathlib4 is large: whether the change rebuilds
`LARGE_MODULES` Mathlib modules or more. The `route` job of mathlib4's
`build_template.yml` runs it, and the `build` job picks its runner label
from the verdict. README.md in this directory describes the heuristic.

Environment: `REPO` (owner/name), `HEAD_SHA` (the commit to build) and
`GH_TOKEN` (for `gh api`).

Appends `large=true|false` and `modules=<count>` to `$GITHUB_OUTPUT`,
prints the verdict as a notice annotation, and appends it to
`$GITHUB_STEP_SUMMARY`. Any error gives `large=false`.
"""

import io
import json
import os
import subprocess
import sys
import traceback
import zipfile

from predict_cascade import cascade, read_graph

LARGE_MODULES = 2000
COMPARE_FILE_LIMIT = 300
MASTER_WORKFLOW = "continuous integration"

REPO = os.environ.get("REPO", "")
HEAD_SHA = os.environ.get("HEAD_SHA", "")


def api(path, binary=False):
    """The response of the GitHub API endpoint `path`, with the token of `gh`."""
    result = subprocess.run(
        ["gh", "api", path], capture_output=True, check=True, timeout=30, text=not binary
    )
    return result.stdout


def api_json(path):
    return json.loads(api(path))


def master_run(sha):
    """The id of the master build of commit `sha`, or None."""
    runs = api_json(f"repos/{REPO}/actions/runs?head_sha={sha}&event=push&branch=master")
    return next((r["id"] for r in runs["workflow_runs"] if r["name"] == MASTER_WORKFLOW), None)


def graph_url(run):
    """The download URL of the import graph of run `run`, or None."""
    artifacts = api_json(f"repos/{REPO}/actions/runs/{run}/artifacts?name=import-graph-dot")
    return next((a["archive_download_url"] for a in artifacts["artifacts"] if not a["expired"]), None)


def build_succeeded(run):
    jobs = api_json(f"repos/{REPO}/actions/runs/{run}/jobs")["jobs"]
    return any(j["name"].endswith("/ Build") and j["conclusion"] == "success" for j in jobs)


def ancestors(sha):
    """The five commits before `sha` in its history, newest first."""
    return [c["sha"] for c in api_json(f"repos/{REPO}/commits?sha={sha}&per_page=6")[1:]]


def compare(base):
    """The merge-base, the number of changed files and the changed paths between `base` and the head."""
    data = api_json(f"repos/{REPO}/compare/{base}...{HEAD_SHA}")
    files = data.get("files", [])
    paths = [f["filename"] for f in files] + [f["previous_filename"] for f in files if "previous_filename" in f]
    return data["merge_base_commit"]["sha"], len(files), paths


def predict():
    """Whether the build is large, the number of modules it rebuilds (or None), and why."""
    merge_base, nfiles, paths = compare("master")
    if nfiles >= COMPARE_FILE_LIMIT:
        return True, None, f"{nfiles} or more changed files"
    base = since = merge_base
    run = master_run(merge_base)
    url = graph_url(run) if run else None
    if url is None:
        cached = run is not None and build_succeeded(run)
        for candidate in ancestors(merge_base):
            run = master_run(candidate)
            url = graph_url(run) if run else None
            if url:
                base = candidate
                break
        else:
            return False, None, f"no master build with an import graph at or before the merge-base {merge_base}"
        if not cached:
            since = base
            _, nfiles, paths = compare(base)
            if nfiles >= COMPARE_FILE_LIMIT:
                return True, None, f"{nfiles} or more files changed since the master build {base}"
    with zipfile.ZipFile(io.BytesIO(api(url, binary=True))) as archive:
        dot = archive.read("import_graph.dot").decode("utf-8")
    modules = cascade(*read_graph(dot.splitlines()), paths)
    if since == base:
        why = f"{modules} Mathlib modules to rebuild since the master build {base}"
    else:
        why = f"{modules} Mathlib modules to rebuild since the merge-base {merge_base} (import graph of the master build {base})"
    return modules >= LARGE_MODULES, modules, why


def append(env, text):
    """Appends `text` to the file named by the environment variable `env`, if set."""
    if os.environ.get(env):
        with open(os.environ[env], "a", encoding="utf-8") as f:
            f.write(text)


def main():
    try:
        large, modules, why = predict()
    except Exception:  # any error gives large=false
        traceback.print_exc()
        large, modules, why = False, None, "no prediction"
    large = "true" if large else "false"
    print(f"Large build: {large} ({why})")
    print(f"::notice title=Large build::{large} ({why})")
    append("GITHUB_STEP_SUMMARY", f"Large build: {large} ({why})\n")
    append("GITHUB_OUTPUT", f"large={large}\nmodules={'' if modules is None else modules}\n")


if __name__ == "__main__":
    main()
