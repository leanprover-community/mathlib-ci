# GitHub Apps Inventory

Mathlib4 installed GitHub Apps page:
<https://github.com/leanprover-community/mathlib4/settings/installations>

For associated Entra Apps for Azure authentication, see [entra-apps.md](entra-apps.md).

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
