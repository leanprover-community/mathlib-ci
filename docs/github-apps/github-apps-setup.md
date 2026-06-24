## How to use a new GitHub app with Azure Key Vault signing

  1. Create the [GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app).
  - Create app in the owning org and set the minimum permissions/events it needs.
  - [Install it](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app) on the target repo(s).
  - Record its App ID.
  - Generate one [private key PEM](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps) (from GitHub App settings). You need this once to seed the [Azure Key Vault](https://learn.microsoft.com/en-us/azure/key-vault/general/overview).

  2. (Optional) Create an [Environment](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments) in the repo via the repo settings.
  - Set up deployment branches and tags that you want the app to be able to run from. (For scheduled jobs, or jobs triggered by `wokrflow_dispatch`, typically `master`.)
  - Later, we will add some secrets or variables to the environment (see step 6).

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
  8. Validate.
