# Azure Create GitHub App Token

```text
GitHub Actions job
  |
  | (1) Request OIDC token (aud = api://AzureADTokenExchange)
  v
GitHub OIDC endpoint
  |
  | OIDC JWT
  v
mint_token.py
|
| (2) Exchange OIDC JWT for Entra access token
`-----------,
             \
             |
             |
             v
           Microsoft Entra token endpoint
             |
             | access_token (scope: https://vault.azure.net/.default)
             |
,------------
|
|(3) Key Vault sign API with non-exportable key
|
`----------->
            |
            |
            v
          Azure Key Vault
            |
            | RSA signature over GitHub App JWT signing input digest
            |
,------------
|
| (4) Signed GitHub App JWT -> mint installation token
|
`-----------,
            |
            |
            v
          GitHub API
            |
,-----------'
|
| (5) Write masked outputs
|
v
Action outputs (`token`, `installation-id`)
```

This local composite action mints a GitHub App installation token without storing the app private key in GitHub Secrets.

## Components

- `action.yml`: action interface (inputs/outputs) and execution wiring.
- `mint_token.py`: end-to-end token flow:
  - build GitHub App JWT payload/header,
  - fetch GitHub OIDC token,
  - exchange OIDC token for Azure access token,
  - call Key Vault `sign`,
  - exchange signed app JWT for GitHub installation token.
- Azure Key Vault key: non-exportable RSA key used only for signing.
- Federated credential in Entra: allows this workflow identity to exchange GitHub OIDC token.

## Avoiding `azure/login`

With `azure/login` + `az keyvault key sign`, startup/login overhead dominates runtime (about 20-30s). For this action we only need one Key Vault data-plane call (`sign`), so pulling in CLI login is unnecessary.

The current implementation uses direct HTTPS calls for the exact same auth chain, with less runtime overhead.

## Inputs used

- `app-id`
- `key-vault-name`
- `key-name`
- `key-version` (optional)
- `azure-client-id`
- `azure-tenant-id`
- `owner` (optional)
- `repositories` (optional)
- `jwt-expiration-seconds` (optional, default: `540`)

## Fixed defaults

The action intentionally hard-codes the following values:

- OIDC audience: `api://AzureADTokenExchange`
- GitHub API URL: `https://api.github.com`

When optional inputs are omitted:

- Key Vault key version: latest (`/keys/{key-name}/sign`)
- Installation resolution: current repository (`GITHUB_REPOSITORY`)
- Installation token scope: full installation permissions (no repository filter)

## Transient failures

The action retries every request in the flow on a 5xx response, a dropped connection,
or a 20s timeout. It makes up to 4 attempts and waits 2s, 4s and 8s between them, so one
request takes at most 94s. A 4xx response is raised at once. A failure that outlives the
retries ends the run with a one-line error naming the hop, not a traceback.

The app JWT is signed with an expiry, so the retries have to fit inside it. The action
mints the JWT only once the Key Vault credentials are in hand, which leaves four requests
running against its clock: the signing call, the org and user installation lookups (the
pair only when `owner` is given), and the token mint. Their worst case is 376s, inside the
JWT's usable lifetime of 480s — `jwt-expiration-seconds` (540) less the 60s skew backdate.
`test_retry_budget_fits_inside_the_jwt_lifetime` holds the timeout and backoff to that
budget. Setting `jwt-expiration-seconds` below 436 gives the guarantee up.

## Security notes
- Workflow must grant `permissions: id-token: write` to allow this action to request a GitHub OIDC token for Entra token exchange.
- Remember keeping Entra federated credential scope tight (repo/workflow/ref constraints).
- Limit Key Vault permissions to the minimum required (`keys/sign` on the target key).
