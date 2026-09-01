# Scripts

This directory contains CI automation scripts consumed by mathlib4 workflows.

Layout:
- `scripts/pr_summary/`: PR summary and import/declaration analysis helpers.
- `scripts/reporting/`: reporting scripts for debt, file size, import lints, and build reports.
- `scripts/maintainer/`: maintainer merge/delegate and PR-testing helper scripts.
- `scripts/nightly/`: nightly branch automation scripts.
- `scripts/zulip/`: Zulip integration scripts.
- `scripts/verification/`: commit verification scripts.

## `pr_summary/`
- `declarations_diff.sh`
  Attempts to find which declarations have been removed and which have been added in the current PR
  with respect to `master`, and posts a comment on github with the result.
- `update_PR_comment.sh` is a script that edits an existing message (or creates a new one).
  It is used by the `PR_summary` workflow to maintain an up-to-date report with a searchable history.
- `count-trans-deps.py`, `import-graph-report.py` and `import_trans_difference.sh` produce various
  summaries of changes in transitive imports that the `PR_summary` message incorporates.
- `dumpReasonableDecls.lean` writes (to `--out`) a sorted list of every "reasonable"
  declaration in the imported environment and (to `--imports-out`) a single-line JSON of
  per-module transitive-import counts (format-compatible with `count-trans-deps.py`). Both
  outputs are produced from a single Lean env load. Invoked post-build by
  `mathlib4`'s `build_template.yml` to populate the `import-graph` artifact.
- `declsDiff.py` takes two pre-computed `decls.txt` files (from
  `dumpReasonableDecls.lean`) and emits the `+NAME` / `-NAME` set-difference plus a Markdown
  body for the `#### Declarations diff` section of the PR summary comment. With `--with-heading`
  it also emits the `#### Declarations diff (Lean)` heading and `<details>` wrap, for splicing
  into the comment.
- `crossrefsDiff.py` takes two `crossrefs.json` exports (from mathlib4's
  `scripts/export_crossrefs.lean`) and emits a Markdown table of the cross-references the PR
  adds, each linked to its entry in the external database. Used by mathlib4's
  `crossref_summary.yml`. The link URL comes from the export (Lean's `Database.url`), so this
  script needs no knowledge of which databases exist.
- `updateDeclsDiffSection.py` PATCHes the `#### Declarations diff (Lean)` block of a PR's
  `### PR summary` comment with the Lean-aware diff, by replacing the region between the
  `<!-- DECLS_DIFF_LEAN_BEGIN/END -->` markers (the regex block is left untouched). Invoked
  post-build by `mathlib4`'s `decls-diff.yml`.
- `olean_diff.py` compares the `.olean` build outputs of two Lean builds and writes two markdown
  reports: a truncated one suitable for posting as a GitHub comment, and a full one for upload as
  a workflow artifact. Modules are classified as having public interface changes (exported
  signatures, declarations, or axioms differ), non-public changes only (proof bodies, docstrings,
  or declaration ranges differ), added, or removed. Takes four arguments:
  `<base_lib_dir> <head_lib_dir> <comment_file> <full_file>`, where `base_lib_dir` and
  `head_lib_dir` are paths to `.lake/build/lib/lean` for the base and PR builds respectively.

## `reporting/`
- `technical-debt-metrics.py`, `technical-debt-metrics.sh`
  Prints information on certain kind of technical debt in Mathlib.
  This output is automatically posted to zulip once a week,
  and is used in the PR summary script.
  The original shell script has been ported to Python, with a thin wrapper left in place for backwards compatibility.
- `long_file_report.sh`
  Prints the list of the 10 longest Lean files in `Mathlib`.
  This output is automatically posted to zulip once a week.
- `late_importers.sh` is the main script used by the `latest_import.yml` action: it formats
  the `linter.minImports` output, summarizing the data in a table.  See the module docs of
  `late_importers.sh` for further details.
- `zulip_build_report.sh` is used to analyse the output from building the nightly-testing-green
  branch with additional linting enabled, and posts a summary of its findings on zulip.

## `maintainer/`
- `get_tlabel.sh` extracts the `t-`label that a PR has (assuming that there is exactly one).
  It is used by the `maintainer_merge` family of workflows to dispatch `maintainer merge` requests
  to the appropriate topic on zulip.
- `maintainer_merge_message.sh` contains a shell script that produces the Zulip message for a
  `maintainer merge`/`maintainer delegate` comment.
- `lean-pr-testing-comments.sh`
  Generate comments and labels on a Lean or Batteries PR after CI has finished on a
  `*-pr-testing-NNNN` branch.

## `nightly/`
- `create-adaptation-pr.sh` implements some of the steps in the workflow described at
  https://leanprover-community.github.io/contribute/tags_and_branches.html#mathlib-nightly-and-bump-branches
  Specifically, it will:
  - merge `master` into `bump/v4.x.y`
  - create a new branch from `bump/v4.x.y`, called `bump/nightly-YYYY-MM-DD`
  - merge `nightly-testing` into the new branch
  - open a PR to merge the new branch back into `bump/v4.x.y`
  - announce the PR on zulip
  - finally, merge the new branch back into `nightly-testing`, if conflict resolution was required.

  If there are merge conflicts, it pauses and asks for help from the human driver.
- `merge-lean-testing-pr.sh` takes a PR number `NNNN` as argument,
  and attempts to merge the branch `lean-pr-testing-NNNN` into `master`.
  It will resolve conflicts in `lean-toolchain`, `lakefile.lean`, and `lake-manifest.json`.
  If there are more conflicts, it will bail.

## `zulip/`
- `parse_lake_manifest_changes.py` compares two versions of `lake-manifest.json` to report
  dependency changes in Zulip notifications. Used by the `update_dependencies_zulip.yml` workflow
  to show which dependencies were updated, added, or removed, with links to GitHub diffs.
- `reconcile_emojis.py` (CLI entry point) and the `emoji_reconcile/` package keep the emoji
  reactions on Zulip messages about a PR in sync with the PR's actual state
  (open/closed/merged, labels, CI result). Repo-agnostic: everything repo-specific is a JSON
  config (worked examples under `examples/`). In CI it runs via the
  `zulip-emoji-reconcile` composite action; `docs/zulip-emoji-quickstart.md` sets a new repo
  up end to end, and `docs/zulip-emoji-reconcile.md` is the reference (config schema, CI
  wiring, local runs). Tested by `tests/zulip_emoji/`.
- `requirements.txt`
  Python requirements for Zulip integration scripts.

## `verification/`
- `verify_commits.sh` verifies special commits in a PR:
  - **Transient commits** (prefix `transient: `) must have zero net effect on the final tree
  - **Automated commits** (prefix `x <command>`; or legacy `x: <command>`)
    must match the effect of re-running the command.
  Supports `--json` for machine-readable output and `--json-file PATH` to write JSON while
  displaying human-readable output.
- `verify_commits_summary.sh` generates a markdown PR comment from `verify_commits.sh` JSON output.
  Used by CI to post verification summaries on pull requests.

## Usage Notes
- In workflows, scripts are typically run from a checkout path like `ci-tools/`.
- For ad-hoc local runs, execute scripts from a local checkout of this repository.
