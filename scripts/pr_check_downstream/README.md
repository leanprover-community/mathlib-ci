# `pr_check_downstream` scripts

Helper scripts for the mathlib4 `PR check downstream` workflow
(`.github/workflows/pr_check_downstream.yml`).

## Overview

When a mathlib4 PR receives a `/check-downstream …` comment from an
authorised user, the workflow:

1. Calls **`validate_names.sh`** to resolve the requested downstream names
   against the inventory, gate `all` / `all@lkg` to owners/members, and
   fetch the PR's merge ref.
2. Dispatches the heavy validation workflow in
   `leanprover-community/downstream-reports` (no heavy work happens here).
3. Calls **`post_ack_comment.sh`** to leave an acknowledgement comment on
   the PR so the commenter gets immediate feedback that the request was
   accepted.

The actual build results are posted back to the PR by the downstream-reports
workflow as separate per-downstream comments.

## Comment grammar

```
/check-downstream <name>[@lkg][, <name>[@lkg]...]
/check-downstream all          → every enabled downstream, merge-SHA mode
/check-downstream all@lkg      → every enabled downstream, LKG mode (OWNER/MEMBER only)
```

A bare name validates the downstream against the PR's would-be-merged
tree (current mathlib master with the PR applied). The optional `@lkg`
suffix instead cherry-picks the PR's commits onto the downstream's
last-known-good mathlib commit and validates against *that* tree —
yielding a verdict that is independent of current master health. See
`docs/internal/pr-validation-workflow.md` in `downstream-reports` for the
full design.

## Scripts

### `validate_names.sh`

Validates the requested downstream names and resolves the PR's merge ref.

**Called by:** the `Validate downstreams against inventory` step.

**Inputs (CLI flags):**

| Flag | Description |
|------|-------------|
| `--names` | Comma-separated downstream names, or `all` |
| `--author-association` | Value of `github.event.comment.author_association` |
| `--output` | File to append `KEY=VALUE` pairs to (pass `$GITHUB_OUTPUT`) |

**Inputs (env):**

| Variable | Description |
|----------|-------------|
| `PR_NUMBER` | PR number on `leanprover-community/mathlib4` |
| `GITHUB_REPOSITORY` | `owner/repo` of the calling workflow |
| `GH_TOKEN` / `GITHUB_TOKEN` | Token for `gh api` (needs PR read access) |
| `INVENTORY_URL` | *(optional)* Override the inventory URL for testing |

**Outputs (written to `--output`):**

| Key | Description |
|-----|-------------|
| `resolved_names` | Normalised comma-separated list of validated names |
| `head_repo` | `owner/repo` of the PR head (differs from base on forks) |
| `head_sha` | HEAD SHA of the PR branch |
| `merge_sha` | Resolved SHA of `refs/pull/N/merge` (the would-be-merged tree) |

### `post_ack_comment.sh`

Posts (or edits in place) a short acknowledgement comment on the mathlib4 PR.

**Called by:** the `Post / update ack comment` step.

**Inputs (env):**

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | Token with `issues:write` on `leanprover-community/mathlib4` |
| `GITHUB_REPOSITORY` | `owner/repo` of the calling workflow |
| `PR_NUMBER` | PR number |
| `DOWNSTREAMS` | Comma-separated list of downstream names being validated |
| `MERGE_SHA` | Resolved merge SHA (displayed as a short SHA in the comment) |
| `GITHUB_SERVER_URL` | *(standard Actions var)* Used to construct the run link |
| `GITHUB_RUN_ID` | *(standard Actions var)* Used to construct the run link |

The comment is identified by a hidden HTML marker so re-triggers edit the same
comment rather than stacking new ones.
