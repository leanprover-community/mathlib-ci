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
- `olean_diff.py` compares the `.olean` build outputs of two Lean builds and writes two markdown
  reports: a truncated one suitable for posting as a GitHub comment, and a full one for upload as
  a workflow artifact. Modules are classified as having public interface changes (exported
  signatures, declarations, or axioms differ), non-public changes only (proof bodies, docstrings,
  or declaration ranges differ), added, or removed. Takes four arguments:
  `<base_lib_dir> <head_lib_dir> <comment_file> <full_file>`, where `base_lib_dir` and
  `head_lib_dir` are paths to `.lake/build/lib/lean` for the base and PR builds respectively.

## `reporting/`
- `technical-debt-metrics.sh`
  Prints information on certain kind of technical debt in Mathlib.
  This output is automatically posted to zulip once a week.
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
- `zulip_emoji_reactions.py` is called
  * every time a `bors d`, `bors merge` or `bors r` comment is added to a PR,
  * whenever bors merges a PR,
  * whenever a PR is closed or reopened
  * whenever a PR is labelled or unlabelled with `awaiting-author` or `maintainer-merge`
  It looks through all zulip posts containing a reference to the relevant PR
  and will post or update an emoji reaction corresponding to the current PR state to the message.
  This reaction is ✌️ (`:peace_sign:`) for delegated, `:bors:` for PRs sent to bors,
  `:merge` for merged PRs, ✍️ (`:writing:`) for PRs awaiting-author,
  🔨 (`:hammer:`) for maintainer-merged PRs and `:closed-pr:` for closed PRs.
  PRs which were migrated to a fork (as indicated by the `migrated-to-fork` label)
  additionally receive a reaction ... (`skip_forward`).
  Two of these are custom emojis configured on zulip.
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
