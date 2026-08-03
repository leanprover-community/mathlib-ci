# Zulip emoji reconciliation — quickstart

This sets up the reconciler for a GitHub repo: create a Zulip bot, subscribe it to your channels,
store its key as a secret, write a config, and turn on the workflow. For what the tool does and
every config option, see the reference, [`zulip-emoji-reconcile.md`](zulip-emoji-reconcile.md).

Do the steps in order; each produces a value the next one needs.

## Before you start

Make sure you have access to the following:

- **Admin on the GitHub repo** whose PRs you want tracked, so you can add a secret and commit a
  workflow.
- A **Zulip organization** where you can create a bot, and permission to subscribe it to any
  private channels where your repo's PRs are discussed.

## 1. Create the Zulip bot

In the Zulip web or desktop app: **gear icon → Personal settings → Bots → Add a new bot**. (Org
admins can use **Organization settings → Bots** to make the bot org-owned instead.)

- **Bot type: Generic bot.** The tool reads messages and adds reactions through the API, which
  is what a Generic bot allows.
- Name it (e.g. `my-repo PR emoji bot`). Zulip builds the bot's email from the name, like
  `my-repo-pr-emoji-bot@your-org.zulipchat.com`.

Collect three values:

| Value | Where to find it | Used in |
|---|---|---|
| Bot **email** | the **Bots** list | config `zulip.email` |
| **API key** | the bot's row → **manage bot** icon → **API key** → **copy** | GitHub secret (step 3) |
| **Site** URL | your realm address, `https://your-org.zulipchat.com` | config `zulip.site` |

The API key is a secret, and only the bot's owner can see it. The email and site are not secret;
they go in the config file in your repo.

## 2. Subscribe the bot to its channels

The bot only touches channels it can read.

- **Public channels:** no action needed. The bot reads and reacts to them as an org member.
- **Private (invite-only) channels:** Subscribe the bot to every private channel that carries
  PR discussion so that it can read and react to messages.

To subscribe: **gear icon → Channel settings → All → pick the channel → Subscribers → Add
subscribers**, type the bot's name, **Add**. You need permission to subscribe others to that
channel; an org admin always has it.

**History caveat.** A private channel set to **protected history** only shows subscribers
messages sent after they join. Subscribe the bot before you rely on it there, or it will miss the
older messages. A **shared history** channel has no such gap.

## 3. Store the API key as a GitHub secret

In the repo: **Settings → Secrets and variables → Actions → New repository secret**.

- **Name:** `ZULIP_API_KEY`. The workflow reads `${{ secrets.ZULIP_API_KEY }}`, so match it
  exactly.
- **Value:** the bot's API key from step 1.

If several repos in the same GitHub organization share a single Zulip bot, you can use an
**organization** secret of the same name. Keep the key out of the config file; the config is
committed to the repo.

## 4. Write the config

Copy the minimal example into your repo:

```sh
cp scripts/zulip/examples/generic-minimal-config.json .github/zulip-emoji-config.json
```

Any path works, but `.github/zulip-emoji-config.json` matches the reference workflow templates.
Edit these fields:

- `github_repo` — `owner/name` of the repo whose PRs drive the emoji.
- `zulip.site` / `zulip.email` — the site URL and bot email from step 1.
- `channels` — map the config's logical keys to your Zulip channel names (e.g.
  `"pr_reviews": "PR reviews"`). Include any private channel from step 2 that a rule references.
- `states` — the emoji table. The minimal example covers open/closed/merged plus CI status with
  unicode emoji. Add rows as your project grows.

The full schema (groups, priorities, `sticky`, `suppress_in`, CI check selection, merge-bot
handling) is under [Configuration](zulip-emoji-reconcile.md#configuration) in the reference, with
a worked example in [`mathlib4-config.json`](../scripts/zulip/examples/mathlib4-config.json).

### Finding a custom realm emoji's `emoji_code`

A row for a custom realm emoji needs the emoji's numeric id in `emoji_code`, with
`"reaction_type": "realm_emoji"`. List your realm's emoji with the bot's credentials:

```sh
curl -sSX GET -G https://your-org.zulipchat.com/api/v1/realm/emoji \
    -u your-bot@your-org.zulipchat.com:API_KEY
```

Each emoji is keyed by its numeric id. That id is the `emoji_code`, and its `name` is the
`emoji`:

```jsonc
{ "emoji": { "22134": { "id": "22134", "name": "bors", "deactivated": false, /* … */ } } }
```

So `:bors:` becomes:

```json
{"emoji": "bors", "emoji_code": "22134", "reaction_type": "realm_emoji"}
```

Set both `emoji_code` and `reaction_type`, or neither; one alone is a config error. Unicode emoji
need neither.

## 5. Add the workflow

GitHub only runs a workflow whose `on:` trigger lives in your repo, so commit a small trigger
workflow that calls the composite action. Copy the
[full workflow template](zulip-emoji-reconcile.md#full-workflow-all-triggers) from the reference
to `.github/workflows/zulip_emoji_reconcile.yml`. One job handles every trigger: an hourly sweep
(the self-healing safety net), a manual dispatch, PR label/close/reopen events, and CI status.

Fill in the per-repo values:

- **Action pin:** replace `@<pin a tag or SHA>` with a real `leanprover-community/mathlib-ci` tag
  or commit SHA.
- **Repository guard:** set `if: github.repository == 'your-org/your-repo'` to your repo, so
  forks don't run it.
- **CI workflow name:** set `workflow_run.workflows` to the `name:` of your CI workflow(s). Drop
  the whole `workflow_run` trigger if your config has no `ci` rules.
- **Config path and secret name:** update `config:` and `zulip-api-key:` if you didn't use
  `.github/zulip-emoji-config.json` and `ZULIP_API_KEY`.

If your config has no `label` rules, you can also drop `labeled` / `unlabeled` from the
`pull_request_target` types. The reference explains each trigger and
[why the `pull_request_target` triggers are safe](zulip-emoji-reconcile.md#security).

## 6. Validate with a dry run

Dry-run a new config first. It logs the planned add/remove for every message and writes nothing.

- **From the workflow:** trigger it from the **Actions** tab (**Run workflow**) with the
  `dry-run` input enabled.
- **Locally** (needs a checkout of mathlib-ci, `pip install -r scripts/zulip/requirements.txt`,
  and an authenticated `gh`):

  ```sh
  export ZULIP_API_KEY=<the bot key>
  python scripts/zulip/reconcile_emojis.py \
      --config .github/zulip-emoji-config.json --sweep --dry-run
  ```

  See [Running it locally](zulip-emoji-reconcile.md#running-it-locally) for details.

Read the log. It lists the messages found in your public channels, and a
`Fetched N recent message(s) from a private channel` line for each subscribed private channel
with recent messages — confirmation that step 2 worked. Below that it prints the reactions it
would add or remove.

## 7. Go live

Remove the dry-run flag, and kick off the first run yourself: **Actions → Run workflow** with
`pr` left empty, which sweeps. Only a sweep can catch up the backlog — PR events begin firing
only once the workflow is on the default branch, so nothing that happened earlier has one, and
GitHub can take a while to register a new workflow's hourly schedule. That first sweep writes a
burst of reactions to catch every message up; after that the sweep is quiet.

## Troubleshooting

- **The bot reacts in public channels but not a private one.** It isn't subscribed to that
  channel (step 2), or the channel uses protected history and the messages predate its
  subscription.
- **A custom emoji doesn't appear, or the run reports a config error.** The `emoji_code` is
  missing or wrong, or only one of `emoji_code` / `reaction_type` is set. Both are required
  together. Re-derive the code (step 4).
- **Nothing happens.** Check that the secret name matches the workflow (`ZULIP_API_KEY`), and
  that the `if: github.repository == '…'` guard names your repo, not the template's placeholder.
- **A run for PR #N didn't fix the emoji on another PR's messages.** Expected: an event run
  only examines messages mentioning its own PR. Messages about other PRs — including PRs from
  before the workflow existed, which never get events at all — are repaired by the hourly
  sweep, or immediately by a manual **Run workflow** with `pr` empty. See
  [How it works](zulip-emoji-reconcile.md#how-it-works) in the reference.
- **A private channel floods every sweep.** Lower `sweep-private-messages` on the action (see the
  reference).
