## How to use a GitHub App from a workflow

A workflow mints a GitHub App installation token through the GitHub App
broker, a Cloudflare Worker in the `mathlib-initiative/cache-infrastructure`
repository (`github-app-broker/`). The job sends its OIDC token and the
app name to the broker with the `mint-github-app-token` action, and the
broker answers with an installation token restricted to the repositories
and permissions its policy names for that job. The app's private key is
a binding of the broker. It is not a GitHub secret, and it does not reach
a runner.

  1. Create the [GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app).
  - Create the app in the owning org and set the minimum permissions/events it needs.
  - [Install it](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app) on the target repo(s).
  - Record its App ID.
  - Generate one [private key PEM](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps) from the app settings. The broker operator uploads it once; delete the local copy afterwards.

  2. Register the app in the broker.
  - Open a PR in `cache-infrastructure` that adds an `APPS` entry (the app name, the App ID, and a key binding name) and one `GRANTS` row per workflow that uses the app. A row pins the repository and the workflow file on its default branch, and names the repositories and permissions the token may reach. The broker README documents the row fields.
  - The operator deploys the policy and uploads the key (`keys.md` in the broker).

  3. Add the mint step to the workflow.

```yaml
  permissions:
    id-token: write
    contents: read # only if this job needs checkout/ repo reads

  steps:
    - name: Generate app token
      id: app-token
      uses: leanprover-community/mathlib-ci/.github/actions/mint-github-app-token@<PINNED_SHA>
      with:
        broker-url: ${{ vars.MATHLIB_GITHUB_APP_BROKER_URL }}
        audience: mathlib-github-app-broker
        app: my-new-app
```

  - `MATHLIB_GITHUB_APP_BROKER_URL` is an [organization-wide variable](https://github.com/organizations/leanprover-community/settings/variables/actions).
  - The action masks the token and sets it as the `token` output. Later steps read `${{ steps.app-token.outputs.token }}`.
  - The step fails when the mint fails. A caller with a fallback sets `continue-on-error: true` on the step and tests the `success` output in later steps.

  4. Validate.
  - Run the workflow from the default branch and expect a green run.
  - A `403` in the step log names the app, the `sub`, and the repository the broker saw, so a row that does not match is visible from the job log. A `401` names the token verification error. A `500` is a broker fault that the operator reads in the Worker log.
