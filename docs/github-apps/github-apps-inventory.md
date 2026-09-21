# GitHub Apps Inventory

Mathlib4 installed GitHub Apps page:
<https://github.com/leanprover-community/mathlib4/settings/installations>

Organization-wide installations:
<https://github.com/organizations/leanprover-community/settings/installations>

For associated Entra Apps for Azure authentication, see [entra-apps.md](entra-apps.md).

## Apps used by mathlib4 workflows

| GitHub App | What It Does | Workflows Using It |
|---|---|---|
| `mathlib-merge-conflicts` | Bot identity with PR write access for conflict-status labeling/commenting. | `merge_conflicts.yml` |
| `mathlib-dependent-issues` | Bot identity with PR/issue mutation access for dependency-tracking labels. | `dependent-issues.yml` |
| `mathlib-nolints` | Bot identity with contents/PR write access for repository maintenance PRs. | `nolints.yml`, `remove_deprecated_decls.yml` |
| `mathlib-update-dependencies` | Bot identity with contents/PR/issue write access for dependency-update automation. | `update_dependencies.yml`, `update_dependencies_zulip.yml` |
| `mathlib-nightly-testing` | Bot identity with cross-repo write access for nightly branch and release maintenance. | `nightly_bump_and_merge.yml`, `nightly_detect_failure.yml`, `nightly_merge_master.yml` |
| `mathlib-triage` | Bot identity with PR/issue write access for maintainer command label/state mutations. | `maintainer_bors.yml`, `maintainer_bors_wf_run.yml`, `maintainer_merge.yml`, `maintainer_merge_wf_run.yml` |
| `mathlib-auto-merge` | Bot identity with PR/issue write access for merge-queue triggering comments. | `build_template.yml` |
| `mathlib-lean-pr-testing` | Bot identity (in `leanprover` org) for Lean-upstream PR feedback and branch updates. | `build_template.yml`, `nightly_detect_failure.yml` |
| `mathlib-splicebot` | Primary bot identity for splice-bot API operations in mathlib workflows. | `splice_bot_wf_run.yaml` |
| `mathlib-copy-splicebot` | Dedicated bot identity for splice-bot branch pushes to fork/copy targets. | `splice_bot_wf_run.yaml` |
| `lpc-team-check` | Dedicated bot identity for authorization checks (team/repo-permission gating). | `splice_bot_wf_run.yaml` |

## Other apps installed on the organization

Inventory as of 2026-09-21. "Write permissions" is the app's write-level permission set;
"repositories" is where it is installed.

| GitHub App | write permissions | repositories | what it does |
|---|---|---|---|
| `mathlib-bors` | checks, contents, issues, pages, pull_requests, statuses, workflows | mathlib4 and other repos using bors | the bors merge queue, run from `bors-ng` |
| `downstream-lean4` | contents, issues, pull_requests, statuses, workflows | batteries, mathlib4-nightly-testing | Lean's [downstream-lean4](https://github.com/leanprover/downstream-lean4) monorepo CI: pushes adaptations to the `nightly-testing` branches; its actions can also open PRs, set labels and commit statuses |
| `leanprover-community-website` | contents | leanprover-community.github.io | pushes the built site to `master` |
| `crossref-exports-app` | contents | crossref-exports | pushes the exported `@[stacks]`/`@[kerodon]`/`@[wikidata]` cross-references |
| `mailmap-app` | (read only) | mathlib_stats | reads the private `mathlib-mailmap` for the contributor statistics |
| `leanprover-community-intentions` | issues, organization_projects, pull_requests | intentions, project-intentions | the intentions claim bot |
| `downstream-reports-automation` | actions, issues, pull_requests | downstream-reports | downstream compatibility reports |
| `leanprover-community-runners`, `mathlib-initiative-runners` | organization_self_hosted_runners | azure-scripts, mathlib-ci | register the self-hosted CI runners |
| `splicebot-testing` | contents, workflows | SpliceBot-testing-fork | SpliceBot development |
| `queueboard-0`, `queueboard-webhooks` | (read only) | queueboard-core | the review dashboard's data feed |

## Third-party apps

| GitHub App | write permissions | repositories | what it does |
|---|---|---|---|
| `giscus` | discussions | blog | comments on blog posts |
| `pre-commit-ci-lite` | contents, statuses, workflows | mathlib4, mathlib4-nightly-testing | pushes pre-commit fixes to PR branches (`pre-commit-ci/lite-action` in `pre-commit.yml`) |
| `botbaki-review` | issues, pull_requests | mathlib4 | AI review comments |
| `claude` | contents, discussions, issues, pull_requests, workflows | mathlib4 | Claude Code on GitHub; no workflow uses it |
| `gitpod-io` | pull_requests, statuses | mathlib4 | Gitpod prebuilds; Gitpod's classic product has been discontinued |
| `vercel` | administration, checks, contents, deployments, issues, pull_requests, repository_hooks, statuses, workflows | mathlib-changelog | deploys mathlib-changelog.org |
| `render` | actions, checks, deployments, environments, issues, pull_requests, repository_hooks, statuses | mathlib-changelog | an earlier deployment target of the changelog site; nothing in the repo references it |

Removed in September 2026: Graphite, Mergify, Always Be Closing and the old `bors` app.

## Permissions worth trimming

Checked against every workflow that mints the app's token:

- `mathlib-bors`: `pages: write` is not needed; bors-ng's own documentation says "Pages: No access".
- `mathlib-nightly-testing`: `actions: write` is not used; the token only pushes branches and tags, comments on one PR and reads Lean's nightly releases.
- `mathlib-update-dependencies`: `issues: write` is unused today; keep it until the dependency-audit comment workflow (mathlib4#43911) has run once, since PR comments may be posted with this token.
- `render`, `claude`, `gitpod-io`: candidates for uninstalling.

Adding an app: install it on the specific repositories it needs, never "all repositories", set the minimum permissions, and add a row here in the same change.
