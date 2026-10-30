# `validate-lake-manifest`

The Build job in mathlib4 runs this action before it downloads the dependencies.
The action reads lake-manifest.json at a commit through the GitHub API and reports
each problem as an annotation on the file.

Each dependency tracks a branch or tag in `inputRev` and pins a commit in `rev`.
On every run, the action fails when a dependency breaks one of these rules:

- `inputRev` is `main`, `master` or a version tag.
- `url` is a GitHub repository in `leanprover-community` or `leanprover`.
- The pin is a full commit hash.

The base manifest is master's manifest at the merge base of master and the commit
under test. The action also checks each dependency whose `url`, `inputRev` or pin
differs from the base:

- A dependency that tracks a branch pins a commit on that branch. GitHub also serves
  commits from unmerged pull requests by SHA, and this rule rejects them.
- A dependency that tracks a tag pins the commit of that tag.
- When `url` and `inputRev` are unchanged, a dependency that tracks a branch does
  not pin an older commit: the pin is the base pin or a descendant of it. This rule
  catches a contributor who keeps an older pin when they resolve a merge conflict.

With `strict: true`, the action fails when a pin breaks one of these rules, or when
an API error stops the pin checks. Otherwise it only warns, so a PR can test an
unmerged dependency commit. With `allow-older-pins: true`, the older-pin rule only
warns, even with `strict: true`.

## Tests

`tests/validate_lake_manifest/` runs the action against a fake GitHub API.
