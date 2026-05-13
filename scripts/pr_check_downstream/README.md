# `pr_check_downstream` scripts

Helper scripts for the mathlib4 `PR check downstream` workflow
(`.github/workflows/pr_check_downstream.yml`).

## Overview

When a mathlib4 PR receives a `!downstream-check …` comment from an
authorised user (OWNER / MEMBER / COLLABORATOR), the workflow:

1. Calls **`validate_names.sh`** to parse each comma-separated entry,
   validate the bare names against the inventory, and resolve the PR's
   merge ref.
2. Dispatches the heavy validation workflow in
   `leanprover-community/downstream-reports` (no heavy work happens here).
3. Calls **`post_ack_comment.sh`** to leave an acknowledgement comment on
   the PR so the commenter gets immediate feedback that the request was
   accepted.

The actual build results are posted back to the PR by the downstream-reports
workflow as separate per-entry comments.

## Comment grammar

```
!downstream-check <name>[@<rev>] [--merge-branch][, <name>[@<rev>] [--merge-branch] ...]
```

Each comma-separated entry:

| Token              | Meaning                                                                                                                              |
|--------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `<name>`           | Required. Must match an `inventory.downstreams[*].name` (case-sensitive).                                                            |
| `@<rev>`           | Optional. Any git refspec (branch / tag / commit SHA) for the downstream's checkout. Defaults to the inventory's `default_branch`.   |
| `--merge-branch`   | Optional, per entry. Flips that single entry to merge mode — i.e. test against the PR's would-be-merged tree instead of the default. |

**Default mode is LKG**: the PR's commits get cherry-picked onto the
downstream's last-known-good mathlib commit (from the published
`lkg/latest.json` snapshot) and the downstream is built against the
resulting synthetic tree. That yields a verdict independent of current
mathlib master health.

**With `--merge-branch`**: the entry instead validates the downstream
against the PR's `merge_commit_sha` (current mathlib master + the PR
applied, as GitHub computes it). Cheaper because the upstream olean cache
hits cleanly, but a failure may be master's fault, not the PR's.

Examples:

| Comment                                                          | Effect                                                                              |
|------------------------------------------------------------------|-------------------------------------------------------------------------------------|
| `!downstream-check FLT`                                          | FLT, LKG mode, default branch.                                                      |
| `!downstream-check FLT, Toric`                                   | Both LKG mode, default branches.                                                    |
| `!downstream-check FLT@v1.2.3`                                   | FLT at tag `v1.2.3`, LKG mode.                                                      |
| `!downstream-check FLT --merge-branch`                           | FLT in merge mode (default branch).                                                 |
| `!downstream-check FLT@main --merge-branch, Toric@v1`            | FLT at `main` in merge mode, Toric at `v1` in LKG mode.                             |
| `!downstream-check FLT, FLT --merge-branch`                      | FLT tested twice (same checkout) — once in LKG mode, once in merge mode.            |

See `docs/internal/pr-validation-workflow.md` in `downstream-reports` for
the full design.

## Scripts

### `validate_names.sh`

Validates the requested downstream entries and resolves the PR's merge ref.

**Called by:** the `Validate downstreams against inventory` step.

**Inputs (CLI flags):**

| Flag        | Description                                                          |
|-------------|----------------------------------------------------------------------|
| `--names`   | Comma-separated `<name>[@<rev>] [--merge-branch]` entries.           |
| `--output`  | File to append `KEY=VALUE` pairs to (pass `$GITHUB_OUTPUT`).         |

**Inputs (env):**

| Variable                    | Description                                                       |
|-----------------------------|-------------------------------------------------------------------|
| `PR_NUMBER`                 | PR number on `leanprover-community/mathlib4`.                     |
| `GITHUB_REPOSITORY`         | `owner/repo` of the calling workflow.                             |
| `GH_TOKEN` / `GITHUB_TOKEN` | Token for `gh api` (needs PR read access).                        |
| `INVENTORY_URL`             | *(optional)* Override the inventory URL for testing.              |

**Outputs (written to `--output`):**

| Key              | Description                                                                                                                                                                                                                                                                  |
|------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `resolved_names` | Normalised comma-separated entries, preserving each entry's `@<rev>` suffix and `--merge-branch` flag.                                                                                                                                                                       |
| `merge_sha`      | Resolved SHA of `refs/pull/N/merge`. Lives on `leanprover-community/mathlib4` even for fork PRs; the dispatched workflow clones the base repo and derives the PR's base/head from this commit's two parents — no head-repo plumbing needed.                                  |

### `post_ack_comment.sh`

Posts (or edits in place) a short acknowledgement comment on the mathlib4 PR.

**Called by:** the `Post / update ack comment` step.

**Inputs (env):**

| Variable             | Description                                                                                                                  |
|----------------------|------------------------------------------------------------------------------------------------------------------------------|
| `GITHUB_TOKEN`       | Token with `issues:write` on `leanprover-community/mathlib4`.                                                                |
| `GITHUB_REPOSITORY`  | `owner/repo` of the calling workflow.                                                                                        |
| `PR_NUMBER`          | PR number.                                                                                                                   |
| `DOWNSTREAMS`        | Comma-separated validation entries (with `@<rev>` / `--merge-branch` preserved) as resolved by `validate_names.sh`.          |
| `MERGE_SHA`          | Resolved merge SHA (displayed as a short SHA in the comment).                                                                |
| `GITHUB_SERVER_URL`  | *(standard Actions var)* Used to construct the run link.                                                                     |
| `GITHUB_RUN_ID`      | *(standard Actions var)* Used to construct the run link.                                                                     |

The comment is identified by a hidden HTML marker so re-triggers edit the same
comment rather than stacking new ones.
