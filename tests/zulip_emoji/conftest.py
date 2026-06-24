"""Put the emoji_reconcile package on sys.path and provide a sample config fixture."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/zulip contains the `emoji_reconcile` package.
_SCRIPTS_ZULIP = Path(__file__).resolve().parents[2] / "scripts" / "zulip"
sys.path.insert(0, str(_SCRIPTS_ZULIP))

from emoji_reconcile.config import parse_config  # noqa: E402


# A representative mathlib-like config, mirroring docs/zulip-emoji-reconcile.md.
# Priorities encode precedence within the "pr" group: terminal states (merged > closed)
# outrank the label-driven states, and among labels ready-to-merge > delegated >
# awaiting-author.
SAMPLE_CONFIG_DATA = {
    "github_repo": "leanprover-community/mathlib4",
    "merged_title_prefix": "[Merged by Bors] -",
    "zulip": {
        "site": "https://leanprover.zulipchat.com",
        "email": "github-mathlib4-bot@leanprover.zulipchat.com",
    },
    "channels": {
        "pr_reviews": "PR reviews",
        "reviewers": "mathlib reviewers",
        "rss_allow": ["mathlib bors notifications"],
    },
    "states": [
        {"name": "merged", "group": "pr", "priority": 30,
         "source": {"state": "merged"}, "emoji": "merge"},
        {"name": "closed", "group": "pr", "priority": 20,
         "source": {"state": "closed"},
         "emoji": "closed-pr", "emoji_code": "61293", "reaction_type": "realm_emoji"},
        {"name": "ready-to-merge", "group": "pr", "priority": 12,
         "source": {"label": "ready-to-merge"},
         "emoji": "bors", "emoji_code": "22134", "reaction_type": "realm_emoji"},
        {"name": "delegated", "group": "pr", "priority": 11,
         "source": {"label": "delegated"}, "emoji": "peace_sign"},
        {"name": "awaiting-author", "group": "pr", "priority": 10,
         "source": {"label": "awaiting-author"}, "emoji": "writing"},
        {"name": "ci-running", "group": "ci", "source": {"ci": "running"}, "emoji": "yellow"},
        {"name": "ci-success", "group": "ci", "source": {"ci": "success"}, "emoji": "check"},
        {"name": "ci-failure", "group": "ci", "source": {"ci": "failure"}, "emoji": "cross_mark"},
        {"name": "maintainer-merge", "group": None,
         "source": {"label": "maintainer-merge"}, "emoji": "hammer",
         "suppress_in": {"channel": "reviewers", "subject_prefix": "maintainer merge"}},
        {"name": "migrated", "group": None, "sticky": True,
         "source": {"label": "migrated-from-branch"}, "emoji": "skip_forward"},
    ],
}


@pytest.fixture
def sample_config():
    """A parsed mathlib-like Config."""
    return parse_config(SAMPLE_CONFIG_DATA)
