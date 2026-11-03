#!/usr/bin/env python3
"""
Usage: route_build.py

Predicts whether a build of mathlib4 is large. The `route` job of mathlib4's
`build_template.yml` runs this script, and the `build` job picks its runner
label from the verdict. A change near the root of the import graph rebuilds
most of Mathlib in about an hour; a change to a leaf module takes minutes. A
separate label for the long builds lets the runner fleet serve them with
faster hosts and keeps them from delaying the short builds.

The build is large when the change rebuilds `LARGE_MODULES` Mathlib modules
or more: the changed modules and every module that imports one of them,
directly or transitively. On a sample of 127 PR builds, this threshold
marked 50 of the 51 builds that took more than 20 minutes, and 11 of the 76
shorter builds.

- The changed files come from the compare API between `master` and the head.
  The API lists at most 300 files; a longer list is large without a count.
  So is a change to a file in `REBUILD_ALL`.
- The import graph is `import_graph.dot` (`lake exe graph`) in the
  `import-graph-dot` artifact of a master build. The script takes the graph
  of the newest master build at or before the merge-base that has one, among
  the merge-base and its `ANCESTORS` previous commits (`master` is linear).
- When that build is older than the merge-base and the merge-base's build
  step has not succeeded, the cache does not hold the merge-base, so the
  diff starts at the older build's commit.

The count is an upper bound when the cache holds the base build. It does not
see a build that stops at a compile error, or a cache that an earlier run of
the same commit filled.

Environment: `REPO` (owner/name), `HEAD_SHA` (the commit to build) and
`GH_TOKEN` (for `gh api`). Appends `large=true|false` and `modules=<count>`
to `$GITHUB_OUTPUT` and the verdict to `$GITHUB_STEP_SUMMARY`, and prints it
as a notice annotation. Any error gives `large=false`.
"""

import io
import json
import os
import re
import subprocess
import traceback
import zipfile
from collections import defaultdict

LARGE_MODULES = 2000
COMPARE_FILE_LIMIT = 300
# A bors batch lands several commits at once, and only the newest has a master build.
ANCESTORS = 50
REBUILD_ALL = {"lakefile.lean", "lake-manifest.json", "lean-toolchain"}
MASTER_WORKFLOW = "continuous integration"
EDGE = re.compile(r'^\s*"([^"]+)"\s*->\s*"([^"]+)"')

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


def compare(base):
    """The merge-base of `base` and the head, and the changed paths (both names of a rename)."""
    data = api_json(f"repos/{REPO}/compare/{base}...{HEAD_SHA}")
    files = data.get("files", [])
    paths = [f["filename"] for f in files] + [f["previous_filename"] for f in files if "previous_filename" in f]
    return data["merge_base_commit"]["sha"], len(files), paths


def rebuilds_all(nfiles, paths):
    """Why the change is large without a count, or None."""
    if nfiles >= COMPARE_FILE_LIMIT:
        return f"{nfiles} or more changed files"
    return next((f"{p} changed, so every module rebuilds" for p in paths if p in REBUILD_ALL), None)


def master_run(sha):
    """The id of the master build of commit `sha`, or None."""
    runs = api_json(f"repos/{REPO}/actions/runs?head_sha={sha}&event=push&branch=master")
    return next((r["id"] for r in runs["workflow_runs"] if r["name"] == MASTER_WORKFLOW), None)


def graph_url(sha):
    """The download URL of the import graph of the master build of `sha`, or None."""
    run = master_run(sha)
    if run is None:
        return None
    artifacts = api_json(f"repos/{REPO}/actions/runs/{run}/artifacts?name=import-graph-dot")
    return next((a["archive_download_url"] for a in artifacts["artifacts"] if not a["expired"]), None)


def build_succeeded(sha):
    run = master_run(sha)
    jobs = api_json(f"repos/{REPO}/actions/runs/{run}/jobs")["jobs"] if run else []
    return any(j["name"].endswith("/ Build") and j["conclusion"] == "success" for j in jobs)


def history(sha):
    """`sha` and its `ANCESTORS` previous commits, newest first. Lists the ancestors only on demand."""
    yield sha
    commits = api_json(f"repos/{REPO}/commits?sha={sha}&per_page={ANCESTORS + 1}")
    yield from (c["sha"] for c in commits[1:])


def newest_graph(sha):
    """The newest commit in `history(sha)` whose master build has an import graph, and the graph URL."""
    for commit in history(sha):
        if url := graph_url(commit):
            return commit, url
    return None, None


def read_graph(dot):
    """The direct importers of each module in a `lake exe graph` file."""
    dependents = defaultdict(set)
    for line in dot.splitlines():
        if m := EDGE.match(line):
            dependents[m.group(1)].add(m.group(2))
    return dependents


def cascade(dependents, paths):
    """The number of modules that a change to `paths` rebuilds. A module outside the graph counts one."""
    stack = [p[: -len(".lean")].replace("/", ".") for p in paths if p.startswith("Mathlib/") and p.endswith(".lean")]
    seen = set()
    while stack:
        mod = stack.pop()
        if mod not in seen:
            seen.add(mod)
            stack.extend(dependents.get(mod, ()))
    return len(seen)


def predict():
    """Whether the build is large, the number of modules it rebuilds (or None), and why."""
    merge_base, nfiles, paths = compare("master")
    if why := rebuilds_all(nfiles, paths):
        return True, None, why
    base, url = newest_graph(merge_base)
    if url is None:
        return False, None, f"no master build with an import graph at or before the merge-base {merge_base}"
    since = merge_base
    if base != merge_base and not build_succeeded(merge_base):
        since = base
        _, nfiles, paths = compare(base)
        if why := rebuilds_all(nfiles, paths):
            return True, None, f"{why} since the master build {base}"
    with zipfile.ZipFile(io.BytesIO(api(url, binary=True))) as archive:
        dependents = read_graph(archive.read("import_graph.dot").decode("utf-8"))
    modules = cascade(dependents, paths)
    return modules >= LARGE_MODULES, modules, f"{modules} Mathlib modules to rebuild since {since} (import graph of {base})"


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
    print(f"::notice title=Large build::{large} ({why})")
    append("GITHUB_STEP_SUMMARY", f"Large build: {large} ({why})\n")
    append("GITHUB_OUTPUT", f"large={large}\nmodules={'' if modules is None else modules}\n")


if __name__ == "__main__":
    main()
