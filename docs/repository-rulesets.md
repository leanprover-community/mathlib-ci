# Repository rulesets for Mathlib's dependencies, the website and the blog

From [FRO+maintainers > Targeted attacks](https://leanprover.zulipchat.com/#narrow/channel/433171-FRO.2Bmaintainers/topic/Targeted.20attacks).

*Motivation.* Mathlib's own `master` branch has been protected by rulesets for a long time, but the
repositories Mathlib depends on had little or nothing: quote4, ProofWidgets4, plausible and
LeanSearchClient had no protection at all, batteries allowed force pushes, and import-graph only
prevented branch deletion. A compromised maintainer account could therefore push arbitrary code
into a dependency and have it pulled into Mathlib by the next `lake update`. The same applied to
the community website and the blog. The proposal to apply Mathlib-style protection to all of them
was made on 2026-09-18 in the thread above and got seven thumbs up and no objections; it was
applied on 2026-09-21.

*The ruleset.* Each repository has a single ruleset, named `default branch: pull requests only`,
applying to its default branch:

- no deletion, no force push, linear history;
- changes only via pull request, with **zero required approvals** (so a maintainer can still merge
  their own PR immediately; the point is that every change goes through CI and leaves a PR trail,
  not that a second human signs off);
- the repository's CI job is a required status check;
- squash merges only (the repository settings are switched to squash-only as well);
- bypass for repository admins in `pull_request` mode only: an admin can merge a PR with a red
  check in an emergency, but nobody can push to the default branch directly, not even admins.

Classic branch protection on the default branch is removed when the ruleset is applied, so that
the ruleset is the single source of truth.

*Which repositories.* batteries, quote4, ProofWidgets4, plausible, LeanSearchClient, import-graph,
leanprover-community.github.io (default branch `lean4`; its `master` is the built site, pushed by
the website app, and is not covered) and blog (which deploys to `deploy`, likewise not covered).
Mathlib itself keeps its own, stricter rulesets.

*How to apply or update.* `scripts/repository-rulesets/dep-repo-rulesets.sh` in this repository contains the list of
repositories with their required check names, builds the ruleset JSON and creates or updates it. Run it as an
organization owner or repository admin:

```
scripts/repository-rulesets/dep-repo-rulesets.sh                 # dry run: print the ruleset per repository
scripts/repository-rulesets/dep-repo-rulesets.sh --apply         # create or update, and remove classic protection
scripts/repository-rulesets/dep-repo-rulesets.sh --repos blog    # restrict to some repositories
```

To add a repository, add a line `repo|CI check name(s)|true` to the `CONFIG` block. The check
name is the `name:` of the CI job (or the job id when unnamed); it can be read off the checks of
any recent commit on the default branch. Adding a repository to the list is how a new Mathlib
dependency gets protected; do it in the same PR that adds the `require`.

*What it changes for maintainers.* Nobody pushes to the default branch any more; everything is a
PR, which a maintainer may merge themselves once CI is green. Release managers and tag pushers
are unaffected: tags are not covered, and write access suffices to push tags and publish releases.
