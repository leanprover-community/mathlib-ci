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

## Code

The `lake_manifest` package runs as `python3 -m lake_manifest` and uses only the
standard library.

- `manifest.py` parses the dependencies.
- `rules.py` turns each broken rule into a finding with its fix.
- `validate.py` reads the manifests and applies the rules.
- `report.py` sets the level of each finding and writes the annotations.
- `github.py` makes the REST calls, with retries for transient failures.

`tests/validate_lake_manifest/` holds the tests.
