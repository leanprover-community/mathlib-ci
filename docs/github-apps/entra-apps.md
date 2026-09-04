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
  | GitHub Apps - Lean PR Testing | (not currently wired to Azure variable in mathlib4) | mathlib-lean-pr-testing | (no KV key currently used in this repo flow) |
  | GitHub Apps - Crossref Exports | GH_APP_AZURE_CLIENT_ID_CROSSREFS (in mathlib4 environment "crossref-exports") | crossref-exports-app | crossref-exports-app-pk |

## Cache writer identities

These are not GitHub Apps but OIDC-federated Azure workload identities (no Key Vault keys) used by mathlib4 CI to upload the build cache to the `lakecache` storage account.
Each is RBAC-scoped to write one Blob Storage container, which is the enforced trust boundary (see mathlib4 `Cache/SECURITY.md`).

| Entra App | App (client) id variable | Container written |
|---|---|---|
| Mathlib CI Cache Writer - Master | CACHE_MASTER_WRITER_AZURE_APP_ID | `master` |
| Mathlib CI Cache Writer - Non-Master | CACHE_NON_MASTER_WRITER_AZURE_APP_ID | `forks` |
| Mathlib CI Cache Writer - Nightly Testing | CACHE_NIGHTLY_TESTING_WRITER_AZURE_APP_ID | `nightly-testing` |
| Mathlib CI Cache Writer - PR Toolchain Tests | CACHE_PR_TOOLCHAIN_TESTS_WRITER_AZURE_APP_ID | `pr-toolchain-tests` |
