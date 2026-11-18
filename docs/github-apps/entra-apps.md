This document is an inventory of Entra app registrations associated with related GitHub Apps.
Treat each Entra app registration as a security boundary: related GitHub Apps can share one registration (see e.g., "label bots"), while unrelated apps with different privileges should use separate registrations.

| Entra App | App (client) id variable | GitHub Apps Covered | Available keys in KV |
  |---|---|---|---|
  | GitHub Apps - Mathlib Label bots | GH_APP_AZURE_CLIENT_ID_LABEL_BOTS | mathlib-merge-conflicts, mathlib-dependent-issues | mathlib-merge-conflicts-app-pk, mathlib-dependent-issues-app-pk |
  | GitHub Apps - Mathlib PR Writers | GH_APP_AZURE_CLIENT_ID_PR_WRITERS | mathlib-nolints, mathlib-update-dependencies | mathlib-nolints-app-pk, mathlib-update-dependencies-app-pk |
  | GitHub Apps - Mathlib Nightly Testing | GH_APP_AZURE_CLIENT_ID_NIGHTLY_TESTING | mathlib-nightly-testing | mathlib-nightly-testing-app-pk |
  | GitHub Apps - Splicebot | GH_APP_AZURE_CLIENT_ID_SPLICEBOT | mathlib-splicebot, mathlib-copy-splicebot | mathlib-splicebot-app-pk, mathlib-copy-splicebot-app-pk |
  | GitHub Apps - Mathlib Triage | GH_APP_AZURE_CLIENT_ID_TRIAGE | mathlib-triage | mathlib-triage-app-pk |
  | GitHub Apps - Auto Merge | GH_APP_AZURE_CLIENT_ID_CI_AUTO_MERGE | mathlib-auto-merge | mathlib-auto-merge-app-pk |
  | GitHub Apps - Crossref Exports | GH_APP_AZURE_CLIENT_ID_CROSSREFS (in mathlib4 environment "crossref-exports") | crossref-exports-app | crossref-exports-app-pk |

## Cache writer identities

mathlib4 CI uploads the build cache to R2.
Each upload job exchanges its GitHub OIDC token at the cache broker for a short-lived S3-compatible key pair, with the [`mint-s3-credentials`](../../.github/actions/mint-s3-credentials/action.yml) action.
The key pair reaches one container only, which is the enforced trust boundary (see mathlib4 `Cache/SECURITY.md`).

The identities below are not GitHub Apps but OIDC-federated Azure workload identities (no Key Vault keys) for the `lakecache` storage account.
Each is RBAC-scoped to write one Blob Storage container.
The current mathlib4 workflows do not use them.
A release tag or a release branch runs the workflows of its own tree, so an older release line can still use them.

| Entra App | App (client) id variable | Container written | Workflow trees that use it |
|---|---|---|---|
| Mathlib CI Cache Writer - Master | CACHE_MASTER_WRITER_AZURE_APP_ID | `master` | Before mathlib4#44832 (v4.35.x and older) |
| Mathlib CI Cache Writer - Non-Master | CACHE_NON_MASTER_WRITER_AZURE_APP_ID | `forks` | Before mathlib4#44640 (v4.35.0-rc4 and older) |
| Mathlib CI Cache Writer - Nightly Testing | CACHE_NIGHTLY_TESTING_WRITER_AZURE_APP_ID | `nightly-testing` | Before mathlib4#44640 (v4.35.0-rc4 and older) |
| Mathlib CI Cache Writer - PR Toolchain Tests | CACHE_PR_TOOLCHAIN_TESTS_WRITER_AZURE_APP_ID | `pr-toolchain-tests` | Before mathlib4#44640 (v4.35.0-rc4 and older) |
