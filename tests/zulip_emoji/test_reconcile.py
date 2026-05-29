"""Tests for per-message reconciliation: diff, add/remove, sticky, suppression, dry-run."""

from __future__ import annotations

from emoji_reconcile.pr_state import PrState, desired_emoji_set
from emoji_reconcile.reconcile import reconcile_message


class FakeReactor:
    """Records add/remove requests instead of calling Zulip."""

    def __init__(self) -> None:
        self.added: list[dict] = []
        self.removed: list[dict] = []

    def add_reaction(self, request: dict) -> dict:
        self.added.append(request)
        return {"result": "success"}

    def remove_reaction(self, request: dict) -> dict:
        self.removed.append(request)
        return {"result": "success"}


def make_message(reactions=None, recipient="PR reviews", subject="#123: a PR", message_id=500):
    """Build a minimal Zulip message dict."""
    return {
        "id": message_id,
        "display_recipient": recipient,
        "subject": subject,
        "reactions": reactions or [],
    }


def reaction(emoji_name, emoji_code=None, reaction_type=None):
    rx = {"emoji_name": emoji_name, "user_id": 7}
    if emoji_code is not None:
        rx["emoji_code"] = emoji_code
    if reaction_type is not None:
        rx["reaction_type"] = reaction_type
    return rx


def reconcile(pr, config, message, **kwargs):
    """Run desired_emoji_set + reconcile_message together against a FakeReactor."""
    reactor = FakeReactor()
    desired = desired_emoji_set(pr, config)
    result = reconcile_message(message, desired, config, reactor, log=lambda _m: None, **kwargs)
    return result, reactor


class TestAddRemove:
    def test_add_missing_emoji(self, sample_config) -> None:
        pr = PrState.make(123, "open", labels=["awaiting-author"])
        result, reactor = reconcile(pr, sample_config, make_message())
        assert result.added == ["writing"]
        assert reactor.added == [{"message_id": 500, "emoji_name": "writing"}]
        assert reactor.removed == []

    def test_remove_stale_emoji(self, sample_config) -> None:
        # PR now has no labels / no CI, but a writing reaction lingers.
        pr = PrState.make(123, "open")
        msg = make_message(reactions=[reaction("writing")])
        result, reactor = reconcile(pr, sample_config, msg)
        assert result.removed == ["writing"]
        assert reactor.removed == [{"message_id": 500, "emoji_name": "writing"}]
        assert reactor.added == []

    def test_swap_one_for_another(self, sample_config) -> None:
        # awaiting-author -> ready-to-merge: remove writing, add bors.
        pr = PrState.make(123, "open", labels=["ready-to-merge"])
        msg = make_message(reactions=[reaction("writing")])
        result, reactor = reconcile(pr, sample_config, msg)
        assert result.removed == ["writing"]
        assert result.added == ["bors"]
        # bors is a realm emoji: add request carries code + type.
        assert reactor.added == [{
            "message_id": 500, "emoji_name": "bors",
            "emoji_code": "22134", "reaction_type": "realm_emoji",
        }]

    def test_already_correct_is_noop(self, sample_config) -> None:
        pr = PrState.make(123, "open", labels=["awaiting-author"])
        msg = make_message(reactions=[reaction("writing")])
        result, reactor = reconcile(pr, sample_config, msg)
        assert not result.changed
        assert reactor.added == [] and reactor.removed == []

    def test_realm_emoji_removal_carries_code_and_type(self, sample_config) -> None:
        # A stale closed-pr (realm emoji) should be removed with its code/type.
        pr = PrState.make(123, "open")
        msg = make_message(reactions=[reaction("closed-pr", emoji_code="61293", reaction_type="realm_emoji")])
        _result, reactor = reconcile(pr, sample_config, msg)
        assert reactor.removed == [{
            "message_id": 500, "emoji_name": "closed-pr",
            "emoji_code": "61293", "reaction_type": "realm_emoji",
        }]


class TestUnmanagedAndSticky:
    def test_unmanaged_reactions_untouched(self, sample_config) -> None:
        # A human 👍 must never be removed, even when the PR has no desired emoji.
        pr = PrState.make(123, "open")
        msg = make_message(reactions=[reaction("thumbs_up"), reaction("tada")])
        result, reactor = reconcile(pr, sample_config, msg)
        assert reactor.removed == []
        assert not result.changed

    def test_sticky_emoji_never_removed(self, sample_config) -> None:
        # migrated is sticky: present but not "desired" once the label is gone -> keep it.
        pr = PrState.make(123, "open")
        msg = make_message(reactions=[reaction("skip_forward")])
        result, reactor = reconcile(pr, sample_config, msg)
        assert reactor.removed == []
        assert not result.changed

    def test_sticky_still_added_when_desired(self, sample_config) -> None:
        pr = PrState.make(123, "open", labels=["migrated-from-branch"])
        result, reactor = reconcile(pr, sample_config, make_message())
        assert result.added == ["skip_forward"]


class TestSuppression:
    def test_maintainer_merge_suppressed_in_reviewers_thread(self, sample_config) -> None:
        pr = PrState.make(123, "open", labels=["maintainer-merge"])
        msg = make_message(recipient="mathlib reviewers", subject="maintainer merge: foo")
        result, reactor = reconcile(pr, sample_config, msg)
        assert "hammer" in result.suppressed
        assert reactor.added == []

    def test_maintainer_merge_applied_elsewhere(self, sample_config) -> None:
        pr = PrState.make(123, "open", labels=["maintainer-merge"])
        msg = make_message(recipient="PR reviews", subject="#123: foo")
        result, reactor = reconcile(pr, sample_config, msg)
        assert result.added == ["hammer"]
        assert result.suppressed == []

    def test_suppression_is_channel_specific(self, sample_config) -> None:
        # Same subject prefix but wrong channel -> not suppressed.
        pr = PrState.make(123, "open", labels=["maintainer-merge"])
        msg = make_message(recipient="PR reviews", subject="maintainer merge: foo")
        result, _reactor = reconcile(pr, sample_config, msg)
        assert result.added == ["hammer"]


class TestDryRun:
    def test_dry_run_reports_but_does_not_mutate(self, sample_config) -> None:
        pr = PrState.make(123, "open", labels=["ready-to-merge"])
        msg = make_message(reactions=[reaction("writing")])
        result, reactor = reconcile(pr, sample_config, msg, dry_run=True)
        # The plan is reported...
        assert result.removed == ["writing"]
        assert result.added == ["bors"]
        # ...but nothing was actually sent to Zulip.
        assert reactor.added == [] and reactor.removed == []
