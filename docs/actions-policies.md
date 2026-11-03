# Actions policies allowing `pull_request_target`

From [mathlib maintainers > pull_request_target blocked from 2 November](https://leanprover.zulipchat.com/#narrow/channel/180721-mathlib-maintainers/topic/pull_request_target.20blocked.20from.202.20November/with/629116275).

*Motivation.* From 2026-11-02 GitHub blocks the `pull_request_target` trigger in public
repositories unless an Actions event policy explicitly allows it
([changelog](https://github.blog/changelog/2026-09-17-workflow-execution-protections-in-github-actions-generally-available/),
[docs](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target)).
Several of our workflows depend on it, because they need a write token or secrets on pull
requests from forks: CI for fork PRs, PR summaries, labelling, title checks and Zulip
notifications. Without a policy they would stop running.

*The policies.* There is one organization policy per repository, named
`pull_request_target allowlist: <repo>`. Each applies to that repository and to the listed
workflow files only, and allows the listed events. Every other workflow, in these repositories
and elsewhere in the organization, keeps GitHub's default block, so a new workflow using
`pull_request_target` does not run until an organization owner adds it here.

| Repository | Workflows | Allowed events |
|---|---|---|
| mathlib4, mathlib4-nightly-testing | `build_fork.yml`, `PR_summary.yml`, `add_label_from_diff.yaml`, `check_pr_titles.yaml`, `label_new_contributor.yml`, `zulip_emoji_reconcile.yml` | `pull_request_target`, `schedule`, `workflow_dispatch`, `workflow_run` |
| batteries | `labels-from-status.yml` | `pull_request_target` |
| physlib | `PRMessage.yml`, `add_label_from_diff.yaml`, `pr_size_label.yaml` | `pull_request_target`, `push`, `workflow_dispatch` |
| blog | `message_zulip_on_prs.yml` | `pull_request_target` |
| leanprover-community.github.io | `message_zulip_on_prs.yml`, `zulip_emoji_reconcile.yml` | `pull_request_target`, `schedule`, `workflow_dispatch`, `workflow_run` |

The event list is an allowlist for the matched files: they can run only on those events. So
when a listed workflow gains a new trigger, add the event here too, or that trigger will be
blocked. Reusable workflows (`workflow_call`, such as `build_template.yml`) and `workflow_run`
followers (such as `decls-diff.yml`) are not affected by the default block and need no entry.

mathlib4_copy and mathlib-at-ICERM26 also contain `pull_request_target` workflows, but have
Actions disabled, so they are deliberately left out.

*How to apply or update.* `scripts/actions-policies/pull-request-target-policies.sh` contains the
table above, builds the policy JSON and creates or updates the policies. Run it as an
organization owner:

```
scripts/actions-policies/pull-request-target-policies.sh              # dry run: print each policy
scripts/actions-policies/pull-request-target-policies.sh --check      # find unlisted pull_request_target workflows
scripts/actions-policies/pull-request-target-policies.sh --apply      # create (disabled) or update
scripts/actions-policies/pull-request-target-policies.sh --apply --enforce active --repos blog
```

New policies are created disabled; activate them one repository at a time and check Policy
insights at https://github.com/organizations/leanprover-community/settings/actions/policies.
There is no evaluate mode on our plan, so a mistake shows up as a blocked run.

*Adding a workflow.* Before allowing a new `pull_request_target` workflow, check that it does not
check out and run code from the pull request with a write token or secrets. Then add the file and
any new events to the `CONFIG` block in the script, in the same PR as the workflow if possible,
and run `--apply --repos <repo>` after merging.
