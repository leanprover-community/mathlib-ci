## How to use a new GitHub app with Azure Key Vault signing

  1. Create the [GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app).
  - Create app in the owning org and set the minimum permissions/events it needs.
  - [Install it](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app) on the target repo(s).
  - Record its App ID.
  - Generate one [private key PEM](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps) (from GitHub App settings). You need this once to seed the [Azure Key Vault](https://learn.microsoft.com/en-us/azure/key-vault/general/overview).

  2. (Recommended) Create an [Environment](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments) in the repo via the repo settings.
  - Set up deployment branches and tags that you want the app to be able to run from. (For scheduled jobs, or jobs triggered by `wokrflow_dispatch`, typically `master`.)
  - Later, we will add some secrets or variables to the environment (see step 6).

  In the GitHub workflow that will use the GitHub app, add the following:
```yaml
environment:
  name: <environment name>
  deployment: false  
```

  3. Put the app signing key in Azure Key Vault.

  - Create/import a key. Conventionally we name them `<app-slug>-app-pk`.
  - Import the GitHub PEM into a Key Vault key.
  - If you want strict pinning/rotation control, note the key version and pass key-version in workflow later.
  - Delete your local version of the key

  4. Create (or pick an existing) [Entra app registration](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app).

  - Reuse an existing entra app if appropriate or create a new one. See [entra-apps.md](entra-apps.md) for details on existing Entra apps.
  - Add [federated credentials for GitHub Actions](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect):
  - Issuer: `https://token.actions.githubusercontent.com`
  - Audience: api://AzureADTokenExchange
  - Use flexible claim matching for your repo/workflow/ref scope (recommended).

  If you created an environment:
  - For the entity type, choose "Environment" and enter the name of the environment you created in step 2.

  Otherwise, you probably want to use "Branch" and input the name of the branch that's allowed (typically `master`).

  The subject must exactly match the `sub` claim in the OIDC token GitHub sends, e.g. `repo:leanprover-community/mathlib4:environment:<environment name>`.
  - Existing repos like mathlib4 use this name-only subject unless they have opted into [immutable subject claims](https://github.blog/changelog/2026-04-23-immutable-subject-claims-for-github-actions-oidc-tokens/). Repos created, renamed, or transferred on or after July 15, 2026 use the immutable format automatically, which embeds the owner and repo IDs: `repo:leanprover-community@41703605/mathlib4@365697493:environment:<environment name>` (get the IDs with `gh api repos/<owner>/<repo> --jq '.owner.id, .id'`).
  - New credentials created in the Azure portal have been observed to use the immutable format. If the repo still sends name-only subjects, the token exchange fails with `AADSTS700213: No matching federated identity record found`. Fix this by adding a second credential with the name-only subject (use the "Other issuer" scenario if the GitHub form keeps adding the IDs). Keeping the immutable-format credential alongside it is harmless and prepares for a future opt-in.
  - Before opting a repo or the org into immutable subjects, add an immutable-format credential to every Entra app it authenticates to (including the cache writer identities in [entra-apps.md](entra-apps.md)); the toggle changes the subject for all OIDC logins from that repo. See [Microsoft's migration guide](https://learn.microsoft.com/en-us/entra/workload-id/workload-identities-github-immutable-subjects).

  5. Grant [Key Vault RBAC](https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide) to that Entra app.

  - Grant the [Key Vault Crypto User](https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide) role (this allows signing).
  - Scope as tightly as possible: grant permission only for the particular key rather than the whole Key Vault.
    - To do this, navigate to the Key Vault, then Keys, then select the specific Key and add via the "Access Control (IAM)" page

  6. Add GitHub repo config

  - Secret: `<YOUR_APP>_APP_ID` (repo convention keeps app IDs in secrets).
  - Variable: `GH_APP_AZURE_CLIENT_ID_<GROUP>` (new or existing grouping variable). This is the "Application (client) ID" of the Entra App you registered in step 4.
    Consider adding it as an [organization-wide variable](https://github.com/organizations/leanprover-community/settings/variables/actions) if it might be reused by other repos (e.g., `mathlib4-nightly-testing`).

  If you created an environment in step 2, you can put these in the environment you created instead of the repo secrets.

  These:
  - Secret: `LPC_AZ_TENANT_ID`
  - Variable: `MATHLIB_AZ_KEY_VAULT_NAME`

  should already be available as [organization-wide values](https://github.com/organizations/leanprover-community/settings/variables/actions).

  7. Update workflow to mint token via Azure action.

```yaml
  permissions:
    id-token: write
    contents: read # only if this job needs checkout/ repo reads

  steps:
    - name: Generate app token
      id: app-token
      uses: leanprover-community/mathlib-ci/.github/actions/azure-create-github-app-token@<PINNED_SHA>
      with:
        app-id: ${{ secrets.MY_NEW_APP_ID }}
        key-vault-name: ${{ vars.MATHLIB_AZ_KEY_VAULT_NAME }}
        key-name: my-new-app-pk
        azure-client-id: ${{ vars.GH_APP_AZURE_CLIENT_ID_PR_WRITERS }}
        azure-tenant-id: ${{ secrets.LPC_AZ_TENANT_ID }}
        # optional:
        # key-version: <kv key version>
        # owner: leanprover-community
        # repositories: mathlib4
        # jwt-expiration-seconds: "540"
```
  8. Validate.
