# Zulip emoji reconciliation

A repo-agnostic tool that keeps the emoji reactions on Zulip messages about a GitHub PR in
sync with the PR's actual state — open/closed/merged, labels (e.g. `awaiting-author`,
`ready-to-merge`), and CI result. It lives in `scripts/zulip/emoji_reconcile/` with the entry
point `scripts/zulip/reconcile_emojis.py`. Everything specific to a repo (channel names,
which labels map to which emoji, custom realm-emoji codes) is supplied as a config file, so
the same code serves any repo that wants this behavior.

> Status: the engine, CLI, and example configs are in this repo. The packaging as a GitHub
> composite action and the mathlib4 cutover from the older `zulip_emoji_reactions.py` are
> tracked separately.

## How it works

For a given PR, the tool reads the PR's current state from GitHub, computes the *desired* set
of emoji from your config, and then adds or removes reactions on the matching Zulip messages
so they match that set. It is idempotent and self-healing: re-running simply re-asserts the
correct state, so a missed event or transient outage is repaired on the next run. It only
ever touches the *bot's own* reactions, and only for the emoji your config names —
human-added reactions (👍 and the like, but also a human's copy of a managed emoji) are
left alone.

There are two ways to invoke it, sharing the same core:

- **Per PR** (`--pr N`): fetch one PR's state and reconcile its messages. Fast; meant for
  event-driven triggers (a label changed, a PR closed, CI finished).
- **Sweep** (`--sweep`): pull a bounded batch of recent messages, find every PR they
  reference, batch-fetch those PRs from GitHub, and reconcile each. A periodic safety net
  that repairs any drift on its own — a repo running only the sweep still converges to
  correct emoji within the sweep interval.

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
    "reviewers":  "your reviewers channel",     // referenced by suppress_in
    "rss_allow":  ["bot notifications topic"]    // RSS topics to include
  },
  "ci": {
    "check_names": ["continuous integration"]   // substring match; see "How the CI emoji is computed"
  },
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
| `zulip.site` / `zulip.email` | The Zulip realm URL and the bot user's email. The API key is supplied separately (see *Running it*). |
| `channels` | Maps *logical keys* (used elsewhere in the config) to actual Zulip channel names, so renaming a channel is a one-line change. `pr_reviews` enables thread matching; `rss` names the feed channel to skip (default `rss`); `rss_allow` is a list of RSS topics to include rather than skip. |
| `ci.check_names` | Names selecting which checks feed the CI emoji, matched as case-insensitive **substrings** of the check-run name, the workflow name, or the status context (see *How the CI emoji is computed*). Empty means "every check on the head commit"; naming your gating jobs or workflows keeps the emoji from flipping on unrelated checks. |
| `states` | The emoji table — one rule per emoji (below). |

### State rules

Each rule says "when this predicate holds for a PR, this emoji should be present."

| Field | Meaning |
|---|---|
| `name` | Unique label for the rule (logging/debugging only). |
| `source` | The predicate. Exactly one of: `{"label": "..."}`, `{"state": "open\|closed\|merged"}`, or `{"ci": "running\|success\|failure"}`. |
| `emoji` | The Zulip emoji name. |
| `emoji_code`, `reaction_type` | Required together for **custom realm emoji** (`"reaction_type": "realm_emoji"` plus the realm's numeric `emoji_code`). Omit both for standard unicode emoji. |
| `group` | Mutual-exclusion class: at most one emoji from a group is shown at a time. `group: null` is an independent toggle, driven solely by its own predicate. |
| `priority` | Within a group, the matching rule with the highest priority wins (ties broken by config order). |
| `sticky` | If true, the emoji is never removed once present (e.g. a "migrated from a fork" marker). |
| `suppress_in` | Leave this emoji entirely alone (never added, never removed) on messages in a given channel/topic where it would be redundant. Takes `{"channel": "<logical key>", "subject_prefix": "..."}` (or a list). |

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

### Message matching

A Zulip message is associated with a PR if either:

- its body contains a link to that PR (`https://github.com/<repo>/pull/<n>`), or
- it is the *first* message of a thread in the `pr_reviews` channel whose topic references
  `#<n>` (these threads are one-per-PR; only the opener is reacted to).

Messages in the rss channel (`channels.rss`, default `rss`) are skipped unless their topic
is listed in `channels.rss_allow`. PR numbers are matched as whole tokens, so `#1234`
resolves only to PR 1234.

Topic-matched candidates are confirmed against Zulip before reacting (is this really the
oldest message of its topic?), because in a bounded sweep window a long-lived thread's true
opener may predate the window. Confirmations cost one extra read per distinct topic and are
cached within a run; link-matched messages need no confirmation.

## Running it

Requirements: Python with the `zulip` package (`scripts/zulip/requirements.txt`) and an
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
here when validating a new config. `--sweep-messages N` (default 5000) bounds how many recent
messages the sweep scans: one combined window of N across all public channels, plus N per
subscribed private channel. It also sets the effective "recently closed" lookback horizon,
since the oldest message in the batch is as far back as the sweep looks.

## Wiring it into CI

GitHub runs a workflow only if its `on:` trigger lives in that repo's `.github/workflows/`,
so every consuming repo needs at least one small trigger workflow of its own. Each one checks
out mathlib-ci (into a trusted path such as `ci-tools/`), installs the requirements, and runs
`reconcile_emojis.py` against your repo's own config file (e.g. `.github/zulip-emoji-config.json`)
with `ZULIP_API_KEY` in the environment. There is an onboarding ladder; the sweep alone is a
complete solution, and events are a latency optimization on top:

1. **Sweep only** — a `schedule:` (plus `workflow_dispatch` for a manual full resync) running
   `--sweep`. Correct and self-healing within the sweep interval; the simplest setup.
2. **+ PR events** — `pull_request_target: [labeled, unlabeled, closed, reopened]` running
   `--pr <number>`, for few-seconds latency on label/close/merge changes.
3. **+ CI status** — `workflow_run` on your CI workflow(s), running `--pr <number>` for the PR
   at the run's head commit, so the CI emoji updates promptly.

The sweep-only floor (level 1) is a complete workflow on its own:

```yaml
# .github/workflows/zulip_emoji_reconcile.yml
name: Zulip emoji reconcile

on:
  schedule:
    - cron: "37 * * * *"   # hourly safety net (offset to dodge top-of-hour load)
  workflow_dispatch: {}     # manual full resync

permissions:
  contents: read
  pull-requests: read

jobs:
  reconcile:
    runs-on: ubuntu-latest
    steps:
      - name: Check out this repo's config        # for .github/zulip-emoji-config.json
        uses: actions/checkout@v6
        with:
          sparse-checkout: .github/zulip-emoji-config.json
          sparse-checkout-cone-mode: false

      - name: Check out mathlib-ci                 # the reconciler
        uses: actions/checkout@v6
        with:
          repository: leanprover-community/mathlib-ci
          ref: <pin a tag or commit SHA>
          path: ci-tools

      - uses: actions/setup-python@v6
        with:
          python-version: '3.x'

      - run: pip install -r ci-tools/scripts/zulip/requirements.txt

      - name: Reconcile
        env:
          ZULIP_API_KEY: ${{ secrets.ZULIP_API_KEY }}
          GH_TOKEN: ${{ github.token }}
        run: |
          python ci-tools/scripts/zulip/reconcile_emojis.py \
            --config .github/zulip-emoji-config.json --sweep
```

To climb the ladder, add triggers and swap the final command's mode: a `pull_request_target`
job runs `--pr ${{ github.event.pull_request.number }}`, and a `workflow_run` job resolves the
PR from the run's head SHA and passes it to `--pr`. Add `--dry-run` while validating a new
config — it logs the planned changes and writes nothing.

GitHub access: the reconciler needs read access to PR state (`pull-requests: read`,
`contents: read`). In-repo event triggers get this from the default `GITHUB_TOKEN`;
cross-fork `workflow_run` triggers need a token minted from a GitHub App installation.

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
- **GitHub** reads go through `gh api graphql`; the sweep batches up to 50 PRs per query (a few
  low-cost queries in total), so even a full sweep stays well inside the GraphQL hourly point
  budget without extra throttling. A `#N` that turns out not to be a PR (an issue number, a
  deleted PR) is tolerated: the query's partial data is kept and that number is simply
  skipped. Scheduled runs are themselves best-effort — cron can be delayed and is disabled
  after long repo inactivity — which is why the sweep is a safety net and events stay the
  primary path.
