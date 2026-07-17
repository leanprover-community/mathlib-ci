"""Tests for the pure desired_emoji_set resolution."""

from __future__ import annotations

from emoji_reconcile.pr_state import PrState, desired_emoji_set


def desired_names(pr_state, config) -> set[str]:
    """The set of rule names whose emoji should be present."""
    return {rule.name for rule in desired_emoji_set(pr_state, config)}


class TestPrGroupExclusivity:
    def test_open_no_labels_no_ci_is_empty(self, sample_config) -> None:
        pr = PrState.make(1, "open")
        assert desired_names(pr, sample_config) == set()

    def test_single_label(self, sample_config) -> None:
        pr = PrState.make(1, "open", labels=["awaiting-author"])
        assert desired_names(pr, sample_config) == {"awaiting-author"}

    def test_group_picks_highest_priority_among_labels(self, sample_config) -> None:
        # ready-to-merge (12) beats both delegated (11) and awaiting-author (10).
        pr = PrState.make(1, "open", labels=["awaiting-author", "delegated", "ready-to-merge"])
        assert desired_names(pr, sample_config) == {"ready-to-merge"}

    def test_delegated_beats_awaiting_author(self, sample_config) -> None:
        pr = PrState.make(1, "open", labels=["awaiting-author", "delegated"])
        assert desired_names(pr, sample_config) == {"delegated"}

    def test_terminal_state_outranks_labels(self, sample_config) -> None:
        # A merged PR that still carries a ready-to-merge label shows only `merge`.
        pr = PrState.make(1, "merged", labels=["ready-to-merge"])
        assert desired_names(pr, sample_config) == {"merged"}

    def test_merged_outranks_closed(self, sample_config) -> None:
        # PrState should already resolve to "merged"; defense-in-depth on priority.
        pr = PrState.make(1, "merged")
        assert desired_names(pr, sample_config) == {"merged"}

    def test_closed_unmerged(self, sample_config) -> None:
        pr = PrState.make(1, "closed")
        assert desired_names(pr, sample_config) == {"closed"}


class TestCiGroupIndependentOfPr:
    def test_ci_and_pr_emoji_coexist(self, sample_config) -> None:
        pr = PrState.make(1, "open", labels=["ready-to-merge"], ci="success")
        assert desired_names(pr, sample_config) == {"ready-to-merge", "ci-success"}

    def test_ci_group_is_exclusive(self, sample_config) -> None:
        # Only one CI emoji at a time; ci field is a single value so this is structural.
        for value, name in [("running", "ci-running"), ("success", "ci-success"),
                            ("failure", "ci-failure")]:
            pr = PrState.make(1, "open", ci=value)
            assert desired_names(pr, sample_config) == {name}

    def test_ci_none_yields_no_ci_emoji(self, sample_config) -> None:
        pr = PrState.make(1, "open", ci="none")
        assert desired_names(pr, sample_config) == set()


class TestIndependentToggles:
    def test_maintainer_merge_coexists_with_group_emoji(self, sample_config) -> None:
        pr = PrState.make(1, "open", labels=["maintainer-merge", "awaiting-author"])
        assert desired_names(pr, sample_config) == {"maintainer-merge", "awaiting-author"}

    def test_migrated_coexists_with_everything(self, sample_config) -> None:
        pr = PrState.make(
            1, "open",
            labels=["migrated-from-branch", "maintainer-merge", "delegated"],
            ci="failure",
        )
        assert desired_names(pr, sample_config) == {
            "migrated", "maintainer-merge", "delegated", "ci-failure"
        }


class TestOrderingIsStable:
    def test_result_follows_config_declaration_order(self, sample_config) -> None:
        pr = PrState.make(1, "open", labels=["ready-to-merge", "migrated-from-branch"], ci="success")
        names = [rule.name for rule in desired_emoji_set(pr, sample_config)]
        # config order: merged, closed, ready-to-merge, ..., ci-success, ..., migrated
        assert names == ["ready-to-merge", "ci-success", "migrated"]
