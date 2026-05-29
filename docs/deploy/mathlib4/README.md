# mathlib4 dry-run deployment (staged)

These are the files to install into **`leanprover-community/mathlib4`** to run the
reconciler's dry-run sweep against real data, per step 2 of the rollout in
[`../../zulip-emoji-reconcile.md`](../../zulip-emoji-reconcile.md).

They live here (rather than committed in mathlib4) because the working environment that
produced them could not write to the sibling mathlib4 checkout. Apply them by hand.

## Files

| Install into mathlib4 at | Source |
|---|---|
| `.github/workflows/zulip_emoji_reconcile_dryrun.yml` | `zulip_emoji_reconcile_dryrun.yml` (this dir) |
| `.github/zulip-emoji-config.json` | `scripts/zulip/examples/mathlib4-config.json` (this repo) |

The config is intentionally *not* duplicated here — it is the validated example config, so
copy that file directly to avoid drift.

## Apply

From a writable mathlib4 checkout (`MATHLIB_CI` = path to this repo):

```sh
cd /path/to/mathlib4
git switch -c zulip-emoji-reconcile-dryrun
mkdir -p .github/workflows
cp "$MATHLIB_CI/docs/deploy/mathlib4/zulip_emoji_reconcile_dryrun.yml" .github/workflows/
cp "$MATHLIB_CI/scripts/zulip/examples/mathlib4-config.json" .github/zulip-emoji-config.json
git add .github/workflows/zulip_emoji_reconcile_dryrun.yml .github/zulip-emoji-config.json
git commit -m "ci: temporary Zulip emoji reconcile dry-run sweep workflow"
git push -u origin zulip-emoji-reconcile-dryrun
```

## Prerequisites before dispatching

1. **Push the mathlib-ci feature branch.** The workflow checks out mathlib-ci at
   `ref: zulip-emoji-reconcile`, so that branch must exist on
   `origin`/`leanprover-community/mathlib-ci`. (Currently local-only.)
2. The `ZULIP_API_KEY` secret must already be present in mathlib4 (it is — the existing
   emoji workflows use it, for `github-mathlib4-bot@leanprover.zulipchat.com`).

## Run it

GitHub → Actions → "Zulip emoji reconcile (dry-run sweep)" → **Run workflow**.

- Defaults to `mode=sweep`, `dry_run=true`, `sweep_messages=5000` — reads only, writes
  nothing. Read the step log: each message prints its planned `+ adding` / `- removing`
  diff. Confirm it converges, paying attention to the CI-from-check-rollup derivation.
- `mode=pr` + a PR number exercises the single-PR event path.
- Flip `dry_run=false` only once the dry-run output looks correct.

## Cleanup

Delete this workflow once the composite-action trigger stubs land; it is validation-only.
