# Zulip emoji reactions: reconciliation design

Status: **core implemented; deployment pending.** Target consumer:
`leanprover-community/mathlib4` first, generalizable to any community repo.

## Implementation status (where to pick up)

The repo-agnostic engine is built and unit-tested (111 tests) under
`scripts/zulip/emoji_reconcile/`, with the entry point `scripts/zulip/reconcile_emojis.py`:

| Module | Role |
|---|---|
| `config.py` | Per-repo config schema + JSON loader/validator (incl. `ci.check_names`). |
| `pr_state.py` | `PrState` + the pure `desired_emoji_set` (group exclusivity by priority). |
| `github_state.py` | Fetch `PrState` via `gh` GraphQL; CI derived from head-commit check-run rollup. Single + batched (sweep). |
| `messages.py` | Pure message↔PR matching (repo-URL links + first message in a `pr_reviews` thread; `rss_allow`). |
| `zulip_io.py` | Paced `search_pr_messages` (event path) + `fetch_recent_messages` (sweep). |
| `paced_client.py` | Header-based `RateLimitPacer` + `PacedZulipClient`. |
| `reconcile.py` | Per-message diff/add/remove (sticky, suppression, dry-run). |
| `cli.py` | `--pr` / `--sweep` orchestration; `zulip` imported lazily. |

Example configs ship under `scripts/zulip/examples/` (`mathlib4-config.json` is the
maximal one and reproduces the old script's semantics; `generic-minimal-config.json` is the
floor). Tests live in `tests/zulip_emoji/`, run by `.github/workflows/test_zulip_emoji.yml`.

**Not yet done:** the composite action wrapper, the per-repo trigger stubs, the live
dry-run validation on mathlib4, and retiring the old `zulip_emoji_reactions.py`. See
*Workflow / trigger architecture* and *Rollout / phasing* below.

## Background

Zulip messages that reference a GitHub PR carry emoji reactions that mirror the PR's
state: ✌️ delegated, `:bors:` sent to bors, `:merge:` merged, ✍️ awaiting-author,
🔨 maintainer-merge, `:closed-pr:` closed, CI status (🟡/✅/❌), and ⏭️ migrated-from-fork.

Today a single script, `scripts/zulip/zulip_emoji_reactions.py`, applies these. It is
driven by five trigger surfaces in mathlib4 (`zulip_emoji_ci_status`,
`zulip_emoji_closed_pr`, `zulip_emoji_labelling`, `zulip_emoji_merge_delegate`, and the
`maintainer_bors` → `maintainer_bors_wf_run` pair). Each invocation passes an `ACTION`
verb (e.g. `ci-success`, `labeled`, `[Merged by Bors]`) plus the PR number.

### Two problems with the current model

1. **Mathlib-specific.** The repo URL regex, Zulip channel/topic names, the emoji table,
   and realm-specific custom emoji codes are all hardcoded. It cannot serve another repo.

2. **Stateless and delta-based → stale emojis.** The script never asks GitHub what the PR
   state actually is; it trusts the `ACTION` from the triggering event and mutates one
   emoji accordingly. Any dropped event (GitHub downtime, a failed or `continue-on-error`
   run, Zulip rate-limit exhaustion) leaves the wrong emoji stuck forever, with no path to
   self-correction.

### Key insight

Almost every emoji is a pure function of GitHub's *current* PR state. In particular the
bors states (`ready-to-merge`, `delegated`) are already stored as PR **labels**. So the
desired emoji set is determined by:

- PR state: open / closed / merged
- Label set: `ready-to-merge`, `delegated`, `awaiting-author`, `maintainer-merge`,
  `migrated-from-branch`
- Latest CI conclusion: running / success / failure

This lets us replace deltas with **reconciliation**: compute the full desired emoji set
from GitHub, then make Zulip match it (add missing, remove extra). Reconciliation is
idempotent and self-healing, and the same code path serves both event triggers and a
scheduled sweep. The staleness fix and the generalization fall out of the same redesign.

## Target architecture

### Core (repo-agnostic, in mathlib-ci)

Two functions:

```
desired_emoji_set(pr_state, config) -> { emoji, ... }     # pure
reconcile_message(message, desired_set, config)           # diff + add/remove on Zulip
```

`pr_state = { status: open|closed|merged, labels: [...], ci: running|success|failure|none }`.

Everything mathlib-specific moves into `config`; the engine only diffs desired-vs-actual
reactions on a Zulip message. Mutual exclusion and independent toggles are expressed in
config via a `group` field: one emoji per group at most; `group: null` means an
independent toggle (e.g. `maintainer-merge`, `migrated`).

### Two entry points, one core

1. **Event path (fast; preserves the few-second latency authors value).**
   Each existing trigger keeps firing, but instead of an `ACTION` delta it says
   *"reconcile PR #N."* The script fetches PR #N's current GitHub state, finds its Zulip
   messages (the existing `#N` search), and reconciles. This is strictly more robust than
   today: even the event path self-corrects, because it reads real state rather than
   trusting the event verb.

2. **Sweep path (hourly safety net; `schedule:` + `workflow_dispatch`).**
   Rather than loop hundreds of per-PR Zulip searches, **invert the loop**: pull recent
   messages from the configured channels once, index them by referenced PR number,
   batch-fetch those PRs' GitHub state via GraphQL (many PRs per query), and reconcile
   each. Naturally bounded to PRs that actually have Zulip messages, i.e. "open + recently
   closed/merged." `workflow_dispatch` provides a manual full-resync escape hatch.

## Configuration (lives in each consuming repo)

mathlib-ci stays repo-agnostic; each community repo ships its own `config.json` and passes
`--config path/to/config.json`. The complete, validated mathlib4 config is checked in at
`scripts/zulip/examples/mathlib4-config.json`; the abbreviated form below is illustrative.

```jsonc
{
  "github_repo": "leanprover-community/mathlib4",
  "zulip": { "site": "https://leanprover.zulipchat.com",
             "email": "github-mathlib4-bot@leanprover.zulipchat.com" },
  "channels": {
    "pr_reviews": "PR reviews",            // first-message-in-thread matching
    "reviewers":  "mathlib reviewers",     // maintainer-merge suppression
    "rss_allow":  ["mathlib bors notifications"]
  },
  "states": [
    {"name":"ready-to-merge","group":"pr","source":{"label":"ready-to-merge"},
       "emoji":"bors","emoji_code":"22134","reaction_type":"realm_emoji"},
    {"name":"delegated","group":"pr","source":{"label":"delegated"},"emoji":"peace_sign"},
    {"name":"awaiting-author","group":"pr","source":{"label":"awaiting-author"},"emoji":"writing"},
    {"name":"closed","group":"pr","source":{"state":"closed"},
       "emoji":"closed-pr","emoji_code":"61293","reaction_type":"realm_emoji"},
    {"name":"merged","group":"pr","source":{"state":"merged"},"emoji":"merge"},
    {"name":"ci-running","group":"ci","source":{"ci":"running"},"emoji":"yellow"},
    {"name":"ci-success","group":"ci","source":{"ci":"success"},"emoji":"check"},
    {"name":"ci-failure","group":"ci","source":{"ci":"failure"},"emoji":"cross_mark"},
    {"name":"maintainer-merge","group":null,"source":{"label":"maintainer-merge"},"emoji":"hammer",
       "suppress_in":{"channel":"reviewers","subject_prefix":"maintainer merge"}},
    {"name":"migrated","group":null,"sticky":true,"source":{"label":"migrated-from-branch"},
       "emoji":"skip_forward"}
  ]
}
```

- `group`: mutual-exclusion class. The reconciler ensures at most one emoji per group,
  matching current state.
- `group: null`: independent toggle, driven solely by its own `source` predicate.
- `sticky: true`: never removed once present (preserves "migrated-from-fork" reactions).
- `suppress_in`: skip this emoji in a specific channel/topic (the redundant
  maintainer-merge emoji in "maintainer merge" reviewer threads).

This table *is* the generalization: a new repo onboards by writing its own. Repo-specific
emojis (bors, maintainer-merge, custom realm emoji codes) are therefore **data, not code** —
they appear only as rows in that repo's config; the engine and the composite action never
name them. The maximal mathlib4 config and a minimal generic one differ only in this table.

Note: PR matching extracts the *full* PR number from `#<n>` / `pull/<n>`, fixing a latent
substring bug in the original (`#123` previously matched inside `#1234`).

## GitHub access

The reconciler needs GitHub **read** to fetch PR state (today the script reads none).
In-repo workflows already have `github.token` with `pull-requests: read` + `contents: read`.
The cross-fork `workflow_run` paths already mint a GitHub App token via the Azure
app-token action, so they are covered too.

## Workflow / trigger architecture

Because reconcile reads real state, the triggering event no longer needs to carry meaning —
it only says *"this PR may have changed."* That collapses the trigger matrix. Two of the
five existing emoji paths disappear entirely:

| Today (delta) | Under reconcile |
|---|---|
| `zulip_emoji_labelling` (labeled/unlabeled) | → "reconcile #N" |
| `zulip_emoji_closed_pr` (closed/reopened) | → "reconcile #N" |
| `zulip_emoji_merge_delegate` (push scan for merges) | **dropped** — `closed`(merged) event + sweep cover it |
| bors-command emoji step in `maintainer_bors_wf_run` | **dropped** — bors adds `ready-to-merge`/`delegated` *labels*, which fire the labeled trigger |
| `zulip_emoji_ci_status` (`workflow_run`) | → "reconcile #N" (CI read from check-run rollup) |

The bors decoupling relies on the fact that labels added via a **GitHub App installation
token** (mathlib-triage) *do* trigger downstream workflows, whereas `GITHUB_TOKEN`-added
labels do not. **Verify this in practice** — it is the one assumption behind dropping the
bors emoji path.

### The hard GitHub constraint

Triggers cannot be centralized: a workflow only runs if its `on:` block lives in that
repo's `.github/workflows/`. So every consuming repo always needs *some* local trigger
YAML. What we centralize is everything below the trigger (checkout/setup/run/PR-resolution).

### Onboarding ladder: 1 to 3 stubs

The sweep is a **complete solution on its own** — a repo with only the scheduled sweep gets
correct, self-healing emojis within ~an hour. Event triggers are purely a latency
optimization layered on top:

1. **Sweep only (1 stub)** — `schedule` + `workflow_dispatch`. Correct, ≤1h latency.
2. **+ PR events (2 stubs)** — `pull_request_target: [labeled, unlabeled, closed, reopened]`
   for few-second latency on labels/close/merge/bors.
3. **+ CI status (3 stubs)** — `workflow_run` for the 🟡/✅/❌ emoji. The CI workflow names
   live in this stub's `on:` block (inherently per-repo data).

### Extraction: a composite action (decided)

The logic is extracted into a composite action in mathlib-ci
(`.github/actions/zulip-emoji-reconcile`), matching the existing `get-mathlib-ci` /
`azure-create-github-app-token` pattern. The per-repo stubs own only `on:` + `runs-on` +
`permissions` + a single `uses:` step. The action is fully generic:

```yaml
inputs:
  config:         # path to the caller repo's config.json   (data)
  zulip-api-key:  # secret
  mode:           # auto | pr | sweep   (default: auto)
  dry-run:        # default false
  mathlib-ci-ref: # pin
# In `auto` mode the run step reads $GITHUB_EVENT_NAME / $GITHUB_EVENT_PATH:
#   pull_request_target → --pr <event.pull_request.number>
#   workflow_run        → resolve PR from head_sha, then --pr
#   schedule/dispatch   → --sweep
```

A reusable workflow (`workflow_call`) was the considered alternative — it would centralize
the `permissions` block too — but the composite action keeps consistency with current
mathlib-ci conventions and is lighter to version.

## Rate limits and pacing

### Zulip

- Limits are **per bot user** (one shared bucket across all calls), plus a per-IP bucket.
  The documented Cloud default is ~200 requests/min/user, but exact values are read from
  response headers rather than assumed.
- On overflow Zulip returns HTTP 429 `{"code":"RATE_LIMIT_HIT","retry-after":<s>}`.
- Per-PR reconcile cost: `2 + N_private` reads (public search + subscriptions + one
  `get_messages` per private channel) plus one request per reaction mutation.
- A naive per-PR sweep over all open PRs would blow the per-minute bucket; the inverted
  loop reduces reads from O(PRs) to O(channels × pages).
- The remaining risk is a **write burst** on the first sweep (or one after an outage),
  since each correction is one write.

### GitHub

- `GITHUB_TOKEN`: 1,000 requests/hour/repository, shared across all the repo's workflow
  runs that hour.
- GitHub App installation token: 5,000/hour minimum, scaling up with installation size.
- GraphQL: 5,000 points/hour; cost is by nodes requested, so batching ~500 PRs in pages of
  100 costs only a few queries / low points. Use GraphQL (not per-PR REST) for the sweep.
- Avoid `/search/issues` (30 req/min, much stricter). `gh pr list` uses GraphQL/pulls, not
  search, so it is fine.
- `schedule:`/cron is best-effort: runs can be delayed or skipped under load, and cron is
  disabled after 60 days of repo inactivity. The hourly sweep is a safety net, not the
  primary path; events stay primary and `workflow_dispatch` is the manual escape hatch.

### Header-based self-pacing (design requirement)

The script must pace itself proactively from rate-limit headers, not only react to 429s.

**Zulip.** Read `X-RateLimit-Remaining` and `X-RateLimit-Reset` from each response.
Maintain a small client wrapper that, before issuing the next call:

- if `remaining` is at or below a configurable floor (e.g. 5), sleep until `reset`
  (plus a small jitter) rather than firing and eating a 429;
- otherwise, when `remaining` is low relative to time-to-`reset`, spread the remaining
  budget across the remaining window (target inter-call delay ≈ `(reset - now) /
  max(remaining, 1)`), so a long write burst self-throttles smoothly instead of sprinting
  into the limit and stalling on `retry-after`.

The existing `retry-after` retry/backoff in `call_with_retry` remains the floor; header
pacing sits in front of it as the steady-state regulator. All reads/writes route through
the wrapper so pacing state (remaining/reset) is shared across the whole run.

**GitHub.** Reads honor `x-ratelimit-remaining` / `x-ratelimit-reset` (REST) and the
GraphQL `rateLimit { remaining resetAt cost }` field; the sweep checks the projected cost
against remaining points before each batch and backs off near exhaustion. Also respect
`Retry-After` / secondary-limit signals if encountered.

**Run safety valves.** A configurable per-run mutation cap and an overall wall-clock budget
bound a single sweep; anything left over is picked up by the next tick. In steady state the
sweep finds almost nothing to change because events already converged within seconds.

## Rollout / phasing (mathlib4 first)

1. ✅ **Build the reconcile core + I/O + CLI** in mathlib-ci (done; 111 tests). The old
   `zulip_emoji_reactions.py` stays in place untouched so nothing breaks mid-migration.
2. **Dry-run sweep on mathlib4.** A temporary `workflow_dispatch`-only validation workflow
   in mathlib4 checks out mathlib-ci *at the feature branch ref*, uses `mathlib4-config.json`,
   and runs `reconcile_emojis.py --sweep --dry-run`. Read the logs to confirm
   desired-vs-actual convergence — especially the CI-from-check-rollup derivation, which has
   no prior art. To set it up in a mathlib4 checkout:

   ```sh
   git switch -c zulip-emoji-reconcile-dryrun
   mkdir -p .github/workflows
   # workflow body: see the dry-run validation workflow committed on this branch
   cp /path/to/mathlib-ci/scripts/zulip/examples/mathlib4-config.json .github/zulip-emoji-config.json
   git add .github/workflows/zulip_emoji_reconcile_dryrun.yml .github/zulip-emoji-config.json
   git commit -m "ci: temporary Zulip emoji reconcile dry-run sweep workflow"
   ```

   Prerequisites: push the mathlib-ci feature branch first (the workflow checks it out at
   that ref), and confirm the `ZULIP_API_KEY` secret exists in mathlib4 (the existing emoji
   workflows already use it). Then dispatch via **Actions → "Zulip emoji reconcile (dry-run
   sweep)" → Run workflow**. Defaults (`mode=sweep`, `dry_run=true`, `sweep_messages=5000`)
   read only and write nothing; flip `dry_run=false` only once the logged plan looks correct.
   Delete the workflow once the composite-action stubs (step 3) land — it is validation-only.
3. **Extract the composite action** once the dry-run looks right.
4. **Replace mathlib4's 5 workflows with 3 stubs** (PR events, CI status, sweep), dropping
   the bors emoji step and the push-to-master scan. Roll out behind the action, dropping
   `--dry-run`, ideally one stub at a time.
5. **Retire** `zulip_emoji_reactions.py` once all triggers use reconcile and the sweep has
   run clean.
6. **Document the config contract** in `scripts/README.md` so a second repo can onboard by
   writing a config (sweep-only stub first).

## Open questions / decisions

- **CI status source** — *decided*: derive the CI emoji from the head commit's check-run
  rollup (`github_state.py`), scoped by `ci.check_names`, so the sweep self-heals it.
  Precedence among checks: running > failure > success > none; cancelled/skipped/neutral
  are ignored (matching the old "cancelled clears the running emoji" behavior).
- **"Recently closed" window** — *decided for now*: implicit in the sweep's `--sweep-messages`
  bound (the older end of the fetched batch is the lookback horizon), rather than an
  explicit date filter. Revisit if the message volume makes the horizon too short.
- **`reopened` / `cancelled`** become trivial under reconcile (no closed / no running emoji
  in the current state), removing several special-case branches.
- **App-token label cascade** — *to verify in practice*: that App-installation-token label
  additions trigger the `pull_request_target: labeled` stub (see *Workflow / trigger
  architecture*).
- **Write-burst safety valves** (per-run mutation cap, wall-clock budget) are specified
  under *Header-based self-pacing* but not yet implemented in the CLI; add before enabling
  live writes on the sweep.

## Decisions locked in

- Full reconcile model (not delta-plus-sweep).
- Per-repo config lives in the consuming repo; mathlib-ci stays repo-agnostic. Repo-specific
  emojis are config data, not engine code.
- Keep fast event triggers **and** add an ~hourly sweep, plus `workflow_dispatch`.
- Header-based self-pacing, in front of the existing retry/backoff.
- Centralize logic in a **composite action**; per-repo **trigger stubs** (1–3, sweep is the
  floor). CI derived from the check-run rollup.
- Validate via **dry-run sweep on mathlib4 first**, then extract, then full 3-stub rollout.
- mathlib4 first; design for generality, exercise with a second repo later.
