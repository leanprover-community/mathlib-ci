## How to use a new GitHub app with Azure Key Vault signing

  1. Create the [GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app).
  - Create app in the owning org and set the minimum permissions/events it needs.
  - [Install it](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app) on the target repo(s).
  - Record its App ID.
  - Generate one [private key PEM](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps) (from GitHub App settings). You need this once to seed the [Azure Key Vault](https://learn.microsoft.com/en-us/azure/key-vault/general/overview).

  2. Put the app signing key in Azure Key Vault.

  - Create/import a key. Conventionally we name them `<app-slug>-app-pk`.
  - Import the GitHub PEM into a Key Vault key.
  - If you want strict pinning/rotation control, note the key version and pass key-version in workflow later.
  - Delete your local version of the key

  3. Create (or pick an existing) [Entra app registration](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app).

  - Reuse an existing entra app if appropriate or create a new one. See [entra-apps.md](entra-apps.md) for details on existing Entra apps.
  - Add [federated credentials for GitHub Actions](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect):
  - Issuer: `https://token.actions.githubusercontent.com`
  - Audience: api://AzureADTokenExchange
  - Use flexible claim matching for your repo/workflow/ref scope (recommended).

  4. Grant [Key Vault RBAC](https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide) to that Entra app.

  - Grant the [Key Vault Crypto User](https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide) role (this allows signing).
  - Scope as tightly as possible: grant permission only for the particular key rather than the whole Key Vault.

  5. Add GitHub repo config

  - Secret: `<YOUR_APP>_APP_ID` (repo convention keeps app IDs in secrets).
  - Variable: `GH_APP_AZURE_CLIENT_ID_<GROUP>` (new or existing grouping variable). Consider adding it as an [organization-wide variable](https://github.com/organizations/leanprover-community/settings/variables/actions) if it might be reused by other repos (e.g., `mathlib4-nightly-testing`)

  These:
  - Secret: `LPC_AZ_TENANT_ID`
  - Variable: `MATHLIB_AZ_KEY_VAULT_NAME`

  should already be available as [organization-wide values](https://github.com/organizations/leanprover-community/settings/variables/actions).

  6. Update workflow to mint token via Azure action.

```
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
  7. Validate.
