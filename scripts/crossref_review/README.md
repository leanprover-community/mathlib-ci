# Cross-reference review

`crossref-pr-comment.py` is the orchestrator called by mathlib4's
`.github/workflows/crossref_review.yml`. It runs the trusted versions of
`extract-crossref-tags.lean` and `crossref-snippet.lean` (located in the
mathlib4 base-branch checkout) against the PR checkout as data, formats a
Markdown table of any added `@[stacks ...]`, `@[kerodon ...]`, or
`@[wikidata ...]` attributes, and exits 0/1/2/3 to tell the workflow
whether to post, edit, or delete the bot comment and whether to fail CI.

See the script's docstring for the exact CLI.
