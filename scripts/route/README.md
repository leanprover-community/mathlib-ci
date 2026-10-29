# Runner pool routing

The `route` job of mathlib4's `build_template.yml` runs `route_build.py` before the `build` job. The script predicts how many Mathlib modules the build compiles and whether the build is large. The `build` job picks its runner label from the verdict.

## Motivation

A build of a branch compiles the modules that the branch changes and every module that imports one of them, directly or transitively. The cache holds the other modules. A change to a module near the root of the import graph rebuilds most of Mathlib, which takes about an hour. A change to a leaf module takes a few minutes. One pool of identical runners serves both badly: the long builds fill the pool and delay the short ones, and hosts that are fast enough for the long builds sit idle during the short ones. The routing sends the long builds to their own label, so a runner fleet can serve that label with faster hosts and scale it on its own.

## Heuristic

The script counts the modules that the change rebuilds, and calls the build large at 2,000 modules or more.

1. The changed files come from the compare API between `master` and the commit to build. The API lists at most 300 files. A longer list marks the build large without a count.
2. A change to `lakefile.lean`, `lake-manifest.json` or `lean-toolchain` rebuilds every module and marks the build large without a count. Otherwise a changed file `Mathlib/A/B.lean` is the module `Mathlib.A.B`, and a file outside `Mathlib/` counts no module.
3. The import graph is the `import_graph.dot` file in the `import-graph-dot` artifact of a master build, which `lake exe graph` writes. The count is the number of changed modules plus every module that imports one of them, directly or transitively. A new module that the graph does not have counts one.
4. The graph comes from the master build of the merge-base. That build uploads the graph right after its build step, a few minutes after the push, and a failed build uploads none. Until then, the graph comes from the newest earlier master build that has one. The cache holds the merge-base once its build step has succeeded, so the diff keeps its start at the merge-base. While that build step runs, or after it failed, the diff starts at the earlier build's commit, so it also counts the master changes after it.

The count is an upper bound of the modules the build compiles when the cache holds the base build: an import that the change removes still counts the importers of the removed module. The count does not see a build that stops at a compile error, or a cache that an earlier build of the same branch filled.

The threshold comes from a sample of 127 PR builds: at 2,000 modules the count marked 50 of the 51 builds that took more than 20 minutes as large, and 11 of the 76 shorter builds. A count weighted by the compile time of each module separates the two groups no better.

## Interface

`route_build.py` reads the environment variables `REPO` (owner/name), `HEAD_SHA` (the commit to build) and `GH_TOKEN` (for `gh api`). It appends `large=true|false` and `modules=<count>` to `$GITHUB_OUTPUT`, prints the verdict as a notice annotation, and appends it to `$GITHUB_STEP_SUMMARY`. On any error it gives `large=false` and prints the traceback. Each API call has a 30 second timeout.

`predict_cascade.py <import_graph.dot> <changed_files.txt>` prints the count for a graph file and a list of changed paths, one per line. `route_build.py` imports it.

`python3 -m pytest tests/route` runs the tests.
