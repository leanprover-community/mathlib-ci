# crossref_review

Privileged orchestrator for the cross-reference tag review pipeline.

The mathlib4 build emits a TSV of every `@[stacks ...]` / `@[kerodon ...]` /
`@[wikidata ...]` tag in the elaborated Mathlib environment (see
`scripts/dump_crossref_tags.lean` in mathlib4). The TSV is shipped via the
[`privilege-escalation-bridge`](https://github.com/leanprover-community/privilege-escalation-bridge)
to a privileged `workflow_run` job
([crossref_review.yml](https://github.com/leanprover-community/mathlib4/blob/master/.github/workflows/crossref_review.yml))
in mathlib4. That job invokes [`post-comment.sh`](./post-comment.sh) here.

`post-comment.sh`:

1. Validates the bridge payload (`PR_NUMBER`, `HEAD_SHA`, TSV size).
2. Skips if the PR head has moved since the build (force-push between
   build and comment).
3. Filters the TSV to records whose source module is among the PR's
   changed `.lean` files (via `gh pr diff --name-only`).
4. Resolves `crossref-render`: prefers the prebuilt binary at
   `$CROSSREF_RENDER_BIN` (the mathlib4 workflow caches this across
   runs); otherwise clones
   [`leanprover-community/external-tags`](https://github.com/leanprover-community/external-tags)
   at the pinned `EXTERNAL_TAGS_SHA` and builds it.
5. Runs `crossref-render`; the rendered Markdown becomes the bot comment.
6. Uses `scripts/pr_summary/update_PR_comment.sh` to post or update the
   PR bot comment.
7. Exits non-zero iff `crossref-render` reported missing tags, so the
   `workflow_run` check turns red.

**Updating the pinned external-tags SHA.** One-line PR to this repo: bump
`EXTERNAL_TAGS_SHA` in `post-comment.sh`. Reviewers diff
`external-tags@<OLD>..<NEW>` to see what changed.

**Trust model.** Nothing from the build artifact is executed. The TSV is
parsed as data; user-controllable fields (tag comments, snippet titles,
snippet descriptions) are Markdown-escaped by `crossref-render` before
interpolation into the comment.
