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
