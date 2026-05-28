# `pr_check_downstream`

The `!downstream-check` directive is implemented as two composite
actions under `.github/actions/`:

| Action | Source | Responsibility |
|---|---|---|
| [`check-downstream-validate`](../../.github/actions/check-downstream-validate/) | `grammar.py`, `validate.py`, `error_comment.py` | Parse the directive entry grammar, resolve the PR's `merge_commit_sha`, and POST a user-facing error comment on grammar violations. |
| [`check-downstream-ack`](../../.github/actions/check-downstream-ack/) | `ack.py` | Render and POST the dispatch acknowledgement comment on the PR after the heavy validation workflow is dispatched. |

## Comment grammar

```
!downstream-check <name-or-slug>[@<rev>] [--merge-branch][, <name-or-slug>[@<rev>] [--merge-branch] ...]
```

Per comma-separated entry:

| Token             | Meaning                                                                                                                                                          |
|-------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `<name-or-slug>`  | Required. Either the downstream's short inventory name (e.g. `FLT`) or its GitHub `owner/repo` slug (e.g. `leanprover-community/FLT`). Resolved downstream by the dispatched workflow. |
| `@<rev>`          | Optional. Any git refspec (branch / tag / commit SHA) for the downstream's checkout.                                                                             |
| `--merge-branch`  | Optional, per entry. Flips that single entry from the default LKG mode to merge mode.                                                                            |

## Where to read next

* [Design doc in `downstream-reports`](https://github.com/leanprover-community/downstream-reports/blob/main/docs/internal/pr-validation-workflow.md) — round-trip topology, LKG vs merge mode, comment shape.
* The two action directories above — each carries its own `action.yml` documenting inputs/outputs.
* `tests/pr_check_downstream/` in this repo — pytest coverage for the parser, error-comment renderer, ack renderer, and `validate.main` orchestration.
