# `validate-lake-manifest`

The Build job in mathlib4 runs this action before it downloads the dependencies.
The action reads lake-manifest.json at a commit through the GitHub API and reports
each problem as an annotation on the file.

Each dependency tracks a branch or tag in `inputRev` and pins a commit in `rev`.
On every run, the action fails when a pin is not a full commit hash.

The other rules make sure that bors can merge the commit. With `strict: true`, the
action fails when a dependency breaks one of them. Otherwise it only warns, so a PR
can test an unmerged dependency commit, for example in a Batteries adaptation PR.

- `inputRev` is `main`, `master` or a version tag.
- `url` is a GitHub repository in `leanprover-community` or `leanprover`.

The base manifest is master's manifest at the merge base of master and the commit
under test. The action also checks each dependency that obeys the rules above and
whose `url`, `inputRev` or pin differs from the base:

- A dependency that tracks a branch pins a commit on that branch. GitHub also serves
  commits from unmerged pull requests by SHA, and this rule rejects them.
- A dependency that tracks a tag pins the commit of that tag.
- When `url` and `inputRev` are unchanged, a dependency that tracks a branch does
  not pin an older commit: the pin is the base pin or a descendant of it. This rule
  catches a contributor who keeps an older pin when they resolve a merge conflict.

An API error that stops these checks counts as a broken rule. With
`allow-older-pins: true`, the older-pin rule only warns, even with `strict: true`.

## Tests

`tests/validate_lake_manifest/` runs the action against a fake GitHub API.
