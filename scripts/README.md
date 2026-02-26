# Scripts

This directory contains trusted CI automation scripts consumed by mathlib4
workflows.

Layout:
- `scripts/pr_summary/`: PR summary and import/declaration analysis helpers.
- `scripts/reporting/`: reporting scripts for debt, file size, import lints, and build reports.
- `scripts/maintainer/`: maintainer merge/delegate and PR-testing helper scripts.
- `scripts/nightly/`: nightly branch automation scripts.
- `scripts/zulip/`: Zulip integration scripts.
- `scripts/verification/`: commit verification scripts.

Current scripts:
- `scripts/pr_summary/count-trans-deps.py`
- `scripts/pr_summary/declarations_diff.sh`
- `scripts/pr_summary/import-graph-report.py`
- `scripts/pr_summary/import_trans_difference.sh`
- `scripts/pr_summary/update_PR_comment.sh`
- `scripts/reporting/technical-debt-metrics.sh`
- `scripts/reporting/long_file_report.sh`
- `scripts/reporting/late_importers.sh`
- `scripts/reporting/zulip_build_report.sh`
- `scripts/maintainer/get_tlabel.sh`
- `scripts/maintainer/maintainer_merge_message.sh`
- `scripts/maintainer/lean-pr-testing-comments.sh`
- `scripts/nightly/create-adaptation-pr.sh`
- `scripts/nightly/merge-lean-testing-pr.sh`
- `scripts/zulip/parse_lake_manifest_changes.py`
- `scripts/zulip/zulip_emoji_reactions.py`
- `scripts/verification/verify_commits.sh`
- `scripts/verification/verify_commits_summary.sh`
