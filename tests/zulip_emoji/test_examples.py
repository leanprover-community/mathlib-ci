"""The shipped example configs must stay valid and behave as documented.

This doubles as the concrete proof that repo-specific emojis are pure data: the same
engine yields mathlib4's full emoji set from one config and a minimal repo's set from
another, with no code differences.
"""

from __future__ import annotations

from pathlib import Path

from emoji_reconcile.config import load_config
from emoji_reconcile.pr_state import PrState, desired_emoji_set

_EXAMPLES = Path(__file__).resolve().parents[2] / "scripts" / "zulip" / "examples"


def names(pr, config):
    return {rule.name for rule in desired_emoji_set(pr, config)}


class TestMathlib4Config:
    def setup_method(self) -> None:
        self.config = load_config(_EXAMPLES / "mathlib4-config.json")

    def test_loads_and_covers_all_emojis(self) -> None:
        assert self.config.github_repo == "leanprover-community/mathlib4"
        assert self.config.managed_emojis == {
            "merge", "closed-pr", "bors", "peace_sign", "writing",
            "yellow", "check", "cross_mark", "hammer", "skip_forward",
        }

    def test_ci_check_names_scope(self) -> None:
        assert self.config.ci_check_names == (
            "Build", "Lint style", "Post-Build Step", "Check workflows",
        )

    def test_reproduces_original_semantics(self) -> None:
        # merged PR shows only the merge emoji, even with a lingering label.
        assert names(PrState.make(1, "merged", labels=["ready-to-merge"]), self.config) == {"merged"}
        # bors label -> bors emoji; coexists with CI and independent toggles.
        assert names(
            PrState.make(1, "open", labels=["ready-to-merge", "maintainer-merge",
                                            "migrated-from-branch"], ci="failure"),
            self.config,
        ) == {"ready-to-merge", "maintainer-merge", "migrated", "ci-failure"}
        # awaiting-author alone.
        assert names(PrState.make(1, "open", labels=["awaiting-author"]), self.config) == {"awaiting-author"}


class TestGenericMinimalConfig:
    def setup_method(self) -> None:
        self.config = load_config(_EXAMPLES / "generic-minimal-config.json")

    def test_loads(self) -> None:
        assert self.config.managed_emojis == {
            "checkered_flag", "wastebasket", "hourglass_flowing_sand", "check_mark", "cross_mark",
        }
        # No bors/maintainer-merge machinery -- those rows simply aren't present.
        assert self.config.ci_check_names == ()

    def test_basic_states(self) -> None:
        assert names(PrState.make(1, "merged"), self.config) == {"merged"}
        assert names(PrState.make(1, "open", ci="success"), self.config) == {"ci-success"}
        assert names(PrState.make(1, "closed", ci="running"), self.config) == {"closed", "ci-running"}
