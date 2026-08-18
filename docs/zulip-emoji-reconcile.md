# Zulip emoji reconciliation

A repo-agnostic tool that keeps the emoji reactions on Zulip messages about a GitHub PR in
sync with the PR's actual state — open/closed/merged, labels (e.g. `awaiting-author`,
`ready-to-merge`), and CI result. Everything specific to a repo (channel names, which labels
map to which emoji, custom realm-emoji codes) is supplied as a JSON config file, so the same
code serves any repo that wants this behavior.

There are two ways to run it, both driven by the same config:

- **The `zulip-emoji-reconcile` composite action** — the normal path for a consuming repo. A
  workflow references it with `uses:`; GitHub checks out mathlib-ci and sets up Python for
  you, so the caller only supplies its own config and a trigger. See *Wiring it into CI*.
- **The CLI** (`scripts/zulip/reconcile_emojis.py`, with the engine under
  `scripts/zulip/emoji_reconcile/`) — for local runs, dry-run validation of a new config, and
  one-off resyncs. See *Running it locally*.

> The engine, CLI, example configs, and the `zulip-emoji-reconcile` composite action all
> live in this repo; mathlib4 consumes them via its `zulip_emoji_reconcile.yml` workflow,
> which supersedes the older per-event `zulip_emoji_*` workflows and their delta-based script.

Setting this up for the first time? The [quickstart](zulip-emoji-quickstart.md) walks the whole
path end to end — creating the Zulip bot, subscribing it to your channels, storing the API
key as a secret, writing the config, and turning on the workflow. This document is the
reference behind it: how the tool behaves and every config knob it exposes.

## How it works

For a given PR, the tool reads the PR's current state from GitHub, computes the *desired* set
of emoji from your config, and then adds or removes reactions on the matching Zulip messages
so they match that set. It is idempotent and self-healing: re-running simply re-asserts the
correct state, so a missed event or transient outage is repaired on the next run. It only
ever touches the *bot's own* reactions, and only for the emoji your config names —
human-added reactions (👍 and the like, but also a human's copy of a managed emoji) are
left alone.

There are two ways to invoke it, sharing the same core:

- **Per PR** (`--pr N`): fetch one PR's state and reconcile the messages that mention *that
  PR*. Fast; meant for event-driven triggers (a label changed, a PR closed, CI finished).
- **Sweep** (`--sweep`): pull a bounded batch of recent messages, find every PR they
  reference, batch-fetch those PRs from GitHub, and reconcile each. A periodic safety net
  that repairs any drift on its own — a repo running only the sweep still converges to
  correct emoji within the sweep interval.

The two scopes are complementary, and reading a run's log with the wrong one in mind is
confusing. A per-PR run searches Zulip only for messages mentioning its PR (plus the PRs
*co-referenced* on those same messages — see *Message matching*); every other message is out
of scope, no matter how stale its emoji. So a per-PR run's log is evidence only about that
PR — messages about other PRs are the sweep's job.

## Configuration

The engine is repo-agnostic; everything repo-specific lives in a JSON config you pass with
`--config`. Two worked examples ship under `scripts/zulip/examples/`:

- `mathlib4-config.json` — the maximal example (bors, maintainer-merge, delegated,
  migrated-from-fork, CI status, custom realm emoji).
- `generic-minimal-config.json` — the floor: open/closed/merged plus CI status, using
  standard unicode emoji only.

A new repo onboards by copying one of these and editing the table. The repo's emoji are pure
data — they appear only as config rows; the engine knows none of them by name.

```jsonc
{
  "github_repo": "your-org/your-repo",
  "zulip": {
    "site":  "https://your-org.zulipchat.com",
    "email": "your-bot@your-org.zulipchat.com"
  },
  "channels": {
    "pr_reviews": "PR reviews",                 // see "Message matching" below
    "rss_allow":  ["bot notifications topic"]    // RSS topics to include
  },
  "ci": {
    "check_names": ["continuous integration"]   // substring match; see "How the CI emoji is computed"
  },
  "merged_title_prefix": "[Merged by Bors] -",  // optional; see "Detecting merges"
  "states": [
    {"name": "merged",  "group": "pr", "priority": 30, "source": {"state": "merged"}, "emoji": "checkered_flag"},
    {"name": "closed",  "group": "pr", "priority": 20, "source": {"state": "closed"}, "emoji": "wastebasket"},
    {"name": "ready-to-merge", "group": "pr", "priority": 12, "source": {"label": "ready-to-merge"},
       "emoji": "bors", "emoji_code": "22134", "reaction_type": "realm_emoji"},

    {"name": "ci-running", "group": "ci", "source": {"ci": "running"}, "emoji": "hourglass_flowing_sand"},
    {"name": "ci-success", "group": "ci", "source": {"ci": "success"}, "emoji": "check_mark"},
    {"name": "ci-failure", "group": "ci", "source": {"ci": "failure"}, "emoji": "cross_mark"},

    {"name": "migrated", "group": null, "sticky": true, "source": {"label": "migrated-from-branch"},
       "emoji": "skip_forward"}
  ]
}
```

### Top-level keys

| Key | Meaning |
|---|---|
| `github_repo` | `owner/name` of the repo whose PRs drive the emoji. |
| `zulip.site` / `zulip.email` | The Zulip realm URL and the bot user's email. The API key is supplied separately (see *Running it locally*). |
| `channels` | Maps *logical keys* (used elsewhere in the config) to actual Zulip channel names, so renaming a channel is a one-line change. `pr_reviews` enables thread matching; `rss` names the feed channel to skip (default `rss`); `rss_allow` is a list of RSS topics to include rather than skip. |
| `ci.check_names` | Names selecting which checks feed the CI emoji, matched as case-insensitive **substrings** of the check-run name, the workflow name, or the status context (see *How the CI emoji is computed*). Empty means "every check on the head commit"; naming your gating jobs or workflows keeps the emoji from flipping on unrelated checks. |
| `merged_title_prefix` | Optional. A PR title prefix that marks a PR merged by a rebasing merge bot (see *Detecting merges*). When set, a **closed** PR whose title starts with this prefix is treated as `merged`. Omit it for repos that merge via the GitHub merge button or merge queue. |
| `states` | The emoji table — one rule per emoji (below). |

### State rules

Each rule says "when this predicate holds for a PR, this emoji should be present."

| Field | Meaning |
|---|---|
| `name` | Unique label for the rule (logging/debugging only). |
| `source` | The predicate. Exactly one of: `{"label": "..."}`, `{"state": "open\|closed\|merged"}`, or `{"ci": "running\|success\|failure"}`. |
| `emoji` | The Zulip emoji name. |
| `emoji_code`, `reaction_type` | Required together for **custom realm emoji** (`"reaction_type": "realm_emoji"` plus the realm's numeric `emoji_code`). Omit both for standard unicode emoji. Supplying only one is a config error. |
| `group` | Mutual-exclusion class: at most one emoji from a group is shown at a time. `group: null` is an independent toggle, driven solely by its own predicate. |
| `priority` | Within a group, the matching rule with the highest priority wins (ties broken by config order). |
| `sticky` | If true, the emoji is never removed once present (e.g. a "migrated from a fork" marker). |
| `suppress_in` | Leave this emoji entirely alone (never added, never removed) on messages in a given channel/topic where it would be redundant. Takes `{"channel": "<logical key>", "subject_prefix": "..."}` (or a list). |

To find a custom emoji's `emoji_code`, list the realm's emoji with the bot's own
credentials — `GET /api/v1/realm/emoji` (`client.get_realm_emoji()`, or
`curl -sSX GET -G <site>/api/v1/realm/emoji -u <bot-email>:<api-key>`). The response keys
each emoji by its numeric id: that id is the `emoji_code`, and the matching `name` is the
`emoji`. The [quickstart](zulip-emoji-quickstart.md#finding-a-custom-realm-emojis-emoji_code)
shows the full command and an example response.

### How the CI emoji is computed

The CI value is not event-driven: every reconcile recomputes it from scratch off the PR's
head commit, in three steps.

1. **Select.** Every check run and legacy status context on the head commit is a candidate.
   If `ci.check_names` is non-empty, a candidate counts only when one of those names is a
   case-insensitive substring of its check-run name, its workflow name, or its status
   context. Substring matching lets you name a job inside a reusable workflow without the
   caller-job prefix: `"Build"` matches the check runs `ci / Build` and `ci (fork) / Build`.
2. **Classify** each selected check: not yet completed → `running`; conclusion `SUCCESS` →
   `success`; `FAILURE`, `TIMED_OUT`, `STARTUP_FAILURE`, or `ACTION_REQUIRED` → `failure`;
   `CANCELLED`, `SKIPPED`, `NEUTRAL`, or `STALE` → no signal at all (as if the check didn't
   exist).
3. **Reduce** the signals to one value by precedence **failure > running > success**, or
   `none` when no selected check produced a signal.

The PR's `ci` value is then matched by `source: {"ci": ...}` rules like any other predicate;
`none` matches no rule, so any stale CI emoji is removed. Consequences worth knowing:

- A failure surfaces as soon as any selected check fails, even while others are still
  running; green appears only once every selected check has finished.
- Skipped jobs never fail the emoji and never block a green. A PR whose selected checks are
  *all* skipped or absent gets **no** CI emoji rather than a green one.
- A check that never runs contributes nothing, so it is harmless to list checks that only
  trigger on some PRs (e.g. a workflow-lint job that runs only when CI workflows change —
  on those PRs it becomes the CI signal, on every other PR it is invisible).
- Because the value is read from live state, the sweep self-heals a missed event.

### Detecting merges

The `merged` state (`source: {"state": "merged"}`) keys off whether GitHub reports the PR as
merged. That works directly for repos that land PRs with the GitHub merge button or merge
queue. It does **not** work for a rebasing merge bot like [bors](https://bors.tech): bors
merges by replaying a PR's commits onto the base branch with new SHAs, so GitHub never sees
the PR's head commit land and marks the PR **closed**, not merged. Left unhandled, those
genuinely-merged PRs would match the `closed` rule and get the closed-PR emoji.

To bridge this, bors renames a PR's title to `[Merged by Bors] - …` when it merges. Set
`merged_title_prefix` to that prefix and a closed PR whose title starts with it is resolved to
`merged` before the rules run — so it matches the `merged` rule, not `closed`. Repos that don't
use such a bot simply omit the key and rely on GitHub's native merged state.

### Message matching

A Zulip message is associated with a PR if either:

- its body contains a link to that PR (`https://github.com/<repo>/pull/<n>`), or
- it is the *first* message of a thread in the `pr_reviews` channel whose topic references
  `#<n>` (these threads are one-per-PR; only the opener is reacted to).

Messages in the rss channel (`channels.rss`, default `rss`) are skipped unless their topic
is listed in `channels.rss_allow`. PR numbers are matched as whole tokens, so `#1234`
resolves only to PR 1234.

Which channels are even in scope depends on the bot's Zulip subscriptions. **Public**
channels are read without subscribing — the bot scans them through the public-channels view
as an org member. A **private (invite-only)** channel, by contrast, is invisible unless the
bot is *subscribed* to it: the bot can neither read nor react there, and the sweep can't even
discover the channel, because it enumerates private channels from the bot's own
subscriptions. Subscribe the bot to every private channel that carries PR discussion — for
mathlib4 that includes `mathlib reviewers`. The [quickstart](zulip-emoji-quickstart.md#2-subscribe-the-bot-to-its-channels)
covers the how-to and the protected-history caveat (a newly subscribed bot may not see a
private channel's older messages).

Topic-matched candidates are confirmed against Zulip before reacting (is this really the
oldest message of its topic?), because in a bounded sweep window a long-lived thread's true
opener may predate the window. Confirmations cost one extra read per distinct topic and are
cached within a run; link-matched messages need no confirmation.

A message that references several PRs (a bors batch, a digest of recent PRs) is reconciled
once, against the **union** of the desired sets of every PR it references — so a digest
linking a merged PR and a closed one carries both emoji. Reconciling such a message per PR
would let each PR's pass remove the emoji the others need, churning the same reactions on
every run. On the event path this means the state of PRs *co-referenced* by the triggering
PR's messages is fetched as well.

## Wiring it into CI

GitHub runs a workflow only if its `on:` trigger lives in that repo's `.github/workflows/`,
so every consuming repo needs at least one small trigger workflow of its own. The reconciler
itself ships as a composite action, `leanprover-community/mathlib-ci/.github/actions/zulip-emoji-reconcile`:
referencing it makes GitHub check out mathlib-ci for you, so a workflow only needs to check
out its own config file and call the action with `uses:` — no manual checkout, Python setup,
or `pip install`. There is an onboarding ladder; the sweep alone is a complete solution, and
events are a latency optimization on top:

1. **Sweep only** — a `schedule:` (plus `workflow_dispatch` for a manual full resync) with
   `sweep: true`. Correct and self-healing within the sweep interval; the simplest setup.
2. **+ PR events** — `pull_request_target: [labeled, unlabeled, closed, reopened]` passing
   `pr: <number>`, for few-seconds latency on label/close/merge changes.
3. **+ CI status** — `workflow_run` on your CI workflow(s), passing `pr: <number>` for the PR
   at the run's head commit, so the CI emoji updates promptly.

Most repos should just wire up all three at once with the full template below; the sweep-only
floor is here for the minimal setup, or if you want to stage the rollout.

Because events are only an optimization, they also only cover what happens *after* they
exist: GitHub fires `pull_request_target` and `workflow_run` solely for a workflow already on
the repo's default branch. A PR that closed, merged, or finished CI before the workflow
landed produces no event — its messages simply wait for the first sweep. And that first sweep
may itself be late: GitHub registers a new workflow's `schedule:` with some lag, so the first
cron slot after merging is often skipped. When onboarding (or after the workflow was broken
for a while), don't wait — run the catch-up by hand with `workflow_dispatch`, leaving `pr`
empty to sweep.

### Sweep-only workflow

The sweep-only floor (level 1) is a complete workflow on its own:

```yaml
# .github/workflows/zulip_emoji_reconcile.yml
name: Zulip emoji reconcile

on:
  schedule:
    - cron: "37 * * * *"   # hourly safety net (offset to dodge top-of-hour load)
  workflow_dispatch: {}     # manual full resync

concurrency:
  # Serialize runs: the reconciler reads live PR state and then writes
  # reactions, so two interleaved runs could re-assert stale state. GitHub
  # keeps only the newest queued run per group (earlier pending runs are
  # canceled), which suits a level-triggered tool — the last run recomputes
  # everything from live state and converges to the final answer.
  group: ${{ github.workflow }}
  cancel-in-progress: false

permissions:
  contents: read
  pull-requests: read

jobs:
  reconcile:
    runs-on: ubuntu-latest
    steps:
      - name: Check out this repo's config        # for .github/zulip-emoji-config.json
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          sparse-checkout: .github/zulip-emoji-config.json
          sparse-checkout-cone-mode: false

      - name: Reconcile
        uses: leanprover-community/mathlib-ci/.github/actions/zulip-emoji-reconcile@<pin a tag or SHA>
        with:
          config: .github/zulip-emoji-config.json
          sweep: true
          zulip-api-key: ${{ secrets.ZULIP_API_KEY }}
          github-token: ${{ github.token }}
```

### Full workflow (all triggers)

The full ladder (levels 1–3) fits in the same single workflow: every trigger funnels into
one job that resolves "which PR(s), or sweep?" from the event and calls the action once.
This is the reference template — it is what
[leanprover-community.github.io runs](https://github.com/leanprover-community/leanprover-community.github.io/pull/890),
with the repo-specific values genericized:

```yaml
# .github/workflows/zulip_emoji_reconcile.yml
name: Zulip emoji reconcile

on:
  schedule:
    - cron: "37 * * * *"   # hourly sweep: the self-healing safety net
  workflow_dispatch:
    inputs:
      pr:
        description: "PR number(s), space-separated; leave empty to sweep recent messages"
        required: false
        default: ""
      dry-run:
        description: "Log planned reaction changes without writing to Zulip"
        type: boolean
        default: false      # default true instead while validating a new config
  pull_request_target:      # label/close/merge/reopen changes, within seconds
    types: [labeled, unlabeled, closed, reopened]
  workflow_run:             # CI start/finish, so the CI emoji updates promptly
    workflows: ["your CI workflow name"]   # the `name:` of your CI workflow file(s)
    types: [requested, completed]

concurrency:
  # Serialize runs: the reconciler reads live PR state and then writes
  # reactions, so two interleaved runs could re-assert stale state. GitHub
  # keeps only the newest queued run per group (earlier pending runs are
  # canceled), which suits a level-triggered tool — the last run recomputes
  # everything from live state and converges to the final answer.
  group: ${{ github.workflow }}
  cancel-in-progress: false

permissions:
  contents: read
  pull-requests: read

jobs:
  reconcile:
    runs-on: ubuntu-latest
    if: github.repository == 'your-org/your-repo'   # skip runs on forks
    steps:
      # On pull_request_target / workflow_run this checks out the *default*
      # branch, so the config (and everything else that runs in this job) is
      # never PR-controlled.
      - name: Check out this repo's config
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          sparse-checkout: .github/zulip-emoji-config.json
          sparse-checkout-cone-mode: false

      - name: Determine PR number(s)
        id: target
        env:
          GH_TOKEN: ${{ github.token }}
          EVENT: ${{ github.event_name }}
          INPUT_PR: ${{ inputs.pr }}
          EVENT_PR: ${{ github.event.pull_request.number }}
          HEAD_SHA: ${{ github.event.workflow_run.head_sha }}
        run: |
          set -euo pipefail
          case "$EVENT" in
            workflow_dispatch) pr="$INPUT_PR" ;;
            pull_request_target) pr="$EVENT_PR" ;;
            workflow_run)
              # PR(s) at the CI run's head commit (works for fork PRs too).
              pr=$(gh api "repos/${GITHUB_REPOSITORY}/commits/${HEAD_SHA}/pulls" \
                     --jq 'map(.number) | join(" ")')
              ;;
            *) pr="" ;;  # schedule -> sweep
          esac
          echo "pr=${pr}" >> "$GITHUB_OUTPUT"

      - name: Reconcile
        # Skip only a workflow_run whose head commit no longer maps to a PR;
        # schedule and PR-less dispatches sweep instead.
        if: steps.target.outputs.pr != '' || github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'
        uses: leanprover-community/mathlib-ci/.github/actions/zulip-emoji-reconcile@<pin a tag or SHA>
        with:
          config: .github/zulip-emoji-config.json
          pr: ${{ steps.target.outputs.pr }}
          sweep: ${{ !steps.target.outputs.pr }}
          dry-run: ${{ inputs.dry-run == true }}
          zulip-api-key: ${{ secrets.ZULIP_API_KEY }}
          github-token: ${{ github.token }}
```

The per-repo knobs are the `workflow_run.workflows` list, the repository guard, the secret
name, and the action pin; a repo whose config has no `label` rules can also drop `labeled` /
`unlabeled` from the `pull_request_target` types. Everything else — the PR resolution, the
sweep/skip logic, the concurrency group, the security model (see [Security](#security) below) —
is repo-agnostic. Choices that generalize, and why:

- `opened` is deliberately not among the `pull_request_target` types: when a PR opens there
  is normally no Zulip message to react to yet (a repo whose bot announces new PRs would
  even race that bot), and the CI `requested` event follows seconds later anyway.
- The `concurrency` group matters most once event triggers exist: bursts (a label added then
  removed, CI finishing as a PR closes) otherwise interleave their read-then-write cycles
  and can re-assert a stale reaction that then sits wrong until the next event or sweep. One
  *shared* group is deliberate: the same PR reaches the workflow under different keys
  (`pull_request.number`, `workflow_run.head_sha`, nothing on a sweep), so per-PR groups
  would leave exactly these cross-trigger races open.
- `workflow_run: requested` fires when a CI run starts (producing the "running" emoji) but
  not for re-runs; `completed` always fires, and the sweep repairs anything left over.

Set `dry-run: true` while validating a new config — it logs the planned changes and writes
nothing. The action's inputs (`config`, `pr`, `sweep`, `dry-run`, `sweep-messages`,
`sweep-private-messages`, `zulip-api-key`, `zulip-email`, `zulip-site`, `github-token`,
`python-version`) are documented in
[`action.yml`](../.github/actions/zulip-emoji-reconcile/action.yml); pin the `@<ref>` to a tag
or commit SHA so a consumer's runs are reproducible.

### Security

The event triggers use `pull_request_target`, which (unlike `pull_request`) runs in the base
repository's context with access to its secrets — including the Zulip API key — even for PRs
from forks. That is what lets a fork PR's label change reach Zulip, but it is also the
trigger's well-known footgun: whatever the job runs, runs with the secret within reach. The
template is arranged so that nothing the job runs is PR-controlled:

- **It never checks out PR code.** On `pull_request_target` and `workflow_run` the checkout
  takes the repository's own default branch (a sparse checkout of just the config file), so the
  config and every script in the job come from trusted `master`, not the PR branch.
- **The token is read-only.** `permissions:` grants `contents: read` and `pull-requests: read`
  and nothing more; the only write the job makes is to Zulip, with the bot key.
- **Event data is passed through `env:`, never interpolated into `run:`.** The PR-resolution
  step reads `github.event.*` values as environment variables and quotes them (`"$VAR"`), so a
  crafted branch name or PR title cannot be expanded into the shell as code. The action passes
  its own inputs the same way.
- **The repository guard** (`if: github.repository == '…'`) keeps the workflow from running in
  forks at all.

Read access to PR state (`pull-requests: read`, `contents: read`) comes from the default
`GITHUB_TOKEN`, and that includes PRs from forks: both base-context triggers
(`pull_request_target` and `workflow_run`) read a fork PR's state with no extra credential. The
one fork wrinkle is that a `workflow_run` event's `pull_requests` payload is empty for fork PRs,
so the *Determine PR number(s)* step resolves the PR from the run's head commit via the API
(`commits/<sha>/pulls`) instead of trusting that payload — which works for forks because the
head commit is reachable in the base repo through the PR's ref. A dedicated token (a PAT or a
GitHub App installation) is only worth adding if a very high-volume repo runs into the default
token's rate limit; this tool's batched reads normally stay well under it.

If you adapt the template, preserve one invariant: don't check out or execute the PR head, and
don't interpolate event text into `run:`. Everything else can change freely.

## Running it locally

The CLI is how you run the tool outside CI — validating a new config with `--dry-run`, doing a
one-off resync, or debugging. In CI, prefer the composite action above, which wraps this same
script. Requirements: Python with the `zulip` package (`scripts/zulip/requirements.txt`) and an
authenticated `gh` CLI for GitHub reads (`GH_TOKEN`/`GITHUB_TOKEN`). The Zulip bot API key is
read from `$ZULIP_API_KEY` (or `--zulip-api-key`); the bot email and site come from the
config unless overridden with `--zulip-email` / `--zulip-site`.

```sh
# Reconcile one PR (event path), previewing changes without writing:
python scripts/zulip/reconcile_emojis.py --config config.json --pr 12345 --dry-run

# Reconcile several PRs at once:
python scripts/zulip/reconcile_emojis.py --config config.json --pr 100 101 102

# Periodic sweep over recent messages:
python scripts/zulip/reconcile_emojis.py --config config.json --sweep
```

`--dry-run` logs the planned add/remove for every message and writes nothing — always start
here when validating a new config. `--sweep-messages N` (default 2000) bounds how many recent
messages the sweep scans: one combined window of N across all public channels, plus a window
per subscribed private channel of `--sweep-private-messages` (default 1000 — kept smaller so
one high-traffic private channel can't add thousands of messages, and their PRs' GitHub
reads, to every sweep). These windows also set the effective "recently closed" lookback
horizon, since the oldest message in a batch is as far back as the sweep looks.

## Rate limits

The two services are handled differently — Zulip calls are actively paced, while GitHub reads
stay cheap by batching:

- **Zulip** limits are per bot user. Every call retries on HTTP 429, sleeping out the
  server's `retry-after` — sufficient because the event path makes only a handful of calls
  and the sweep is a cron job nobody is waiting on. The sweep also inverts the naive loop —
  it fetches recent messages once and indexes them by PR, so reads scale with channels ×
  pages rather than with the number of PRs. The main cost is the one-time write burst on a
  first sweep (or one after an outage), which simply glides through a few retry pauses; in
  steady state events have already converged and the sweep finds little to do.
- **GitHub** reads go through `gh api graphql`; the sweep batches up to 25 PRs per query (a few
  low-cost queries in total), so even a full sweep stays well inside the GraphQL hourly point
  budget without extra throttling. Batches also adapt to GitHub's opaque per-query resource
  limits, which scale with the PRs' actual check data: a batch rejected with
  `RESOURCE_LIMITS_EXCEEDED` is bisected and retried, and a PR too expensive to query even
  alone is skipped with a warning (its messages are left untouched until the next run). A
  `#N` that turns out not to be a PR (an issue number, a deleted PR) is tolerated: the
  query's partial data is kept and that number is simply skipped. Scheduled runs are themselves best-effort — cron can be delayed and is disabled
  after long repo inactivity — which is why the sweep is a safety net and events stay the
  primary path.
