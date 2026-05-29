#!/usr/bin/env python3
"""Entry point for Zulip emoji reconciliation. See emoji_reconcile/ for the implementation.

Examples:
  # Reconcile one PR (event path), dry run:
  python scripts/zulip/reconcile_emojis.py --config config.json --pr 12345 --dry-run

  # Reconcile several PRs (e.g. the merge-scan on a push to master):
  python scripts/zulip/reconcile_emojis.py --config config.json --pr 100 101 102

  # Periodic sweep over recent messages:
  python scripts/zulip/reconcile_emojis.py --config config.json --sweep

Requires the `zulip` package (see scripts/zulip/requirements.txt) and, for GitHub state,
an authenticated `gh` CLI (GH_TOKEN/GITHUB_TOKEN).
"""

import sys

from emoji_reconcile.cli import main

if __name__ == "__main__":
    sys.exit(main())
