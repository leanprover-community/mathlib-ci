"""Tests for message<->PR matching: URL links, pr_reviews threads, rss, dedup."""

from __future__ import annotations

from emoji_reconcile.messages import (
    index_messages_by_pr,
    iter_pr_message_targets,
    messages_for_pr,
)

REPO = "leanprover-community/mathlib4"


def msg(message_id, content="", recipient="general", subject=""):
    return {"id": message_id, "content": content, "display_recipient": recipient,
            "subject": subject, "reactions": []}


def url(n):
    return f"https://github.com/{REPO}/pull/{n}"


class TestUrlMatching:
    def test_link_in_body_matches(self, sample_config) -> None:
        messages = [msg(1, content=f"see {url(123)} please")]
        assert list(iter_pr_message_targets(messages, sample_config)) == [(123, messages[0])]

    def test_full_number_not_prefix(self, sample_config) -> None:
        # A link to PR 1234 must not register as PR 123 (the old substring bug).
        messages = [msg(1, content=url(1234))]
        pairs = list(iter_pr_message_targets(messages, sample_config))
        assert pairs == [(1234, messages[0])]

    def test_multiple_links_one_message(self, sample_config) -> None:
        messages = [msg(1, content=f"{url(10)} and {url(20)}")]
        numbers = {n for n, _ in iter_pr_message_targets(messages, sample_config)}
        assert numbers == {10, 20}

    def test_duplicate_link_yields_once(self, sample_config) -> None:
        messages = [msg(1, content=f"{url(10)} {url(10)}")]
        assert list(iter_pr_message_targets(messages, sample_config)) == [(10, messages[0])]

    def test_other_repo_link_ignored(self, sample_config) -> None:
        messages = [msg(1, content="https://github.com/other/repo/pull/5")]
        assert list(iter_pr_message_targets(messages, sample_config)) == []


class TestPrReviewsThread:
    def test_first_message_in_thread_matches_by_subject(self, sample_config) -> None:
        messages = [msg(1, recipient="PR reviews", subject="#123: a title")]
        assert list(iter_pr_message_targets(messages, sample_config)) == [(123, messages[0])]

    def test_only_first_message_in_thread(self, sample_config) -> None:
        messages = [
            msg(1, recipient="PR reviews", subject="#123: a title"),
            msg(2, recipient="PR reviews", subject="#123: a title"),
        ]
        pairs = list(iter_pr_message_targets(messages, sample_config))
        # Only the opener (id 1) carries the reaction.
        assert pairs == [(123, messages[0])]

    def test_subject_match_only_in_pr_reviews(self, sample_config) -> None:
        # Same subject form but a different channel -> no subject-based match.
        messages = [msg(1, recipient="general", subject="#123: a title")]
        assert list(iter_pr_message_targets(messages, sample_config)) == []

    def test_subject_full_number(self, sample_config) -> None:
        messages = [msg(1, recipient="PR reviews", subject="#1234: t")]
        assert list(iter_pr_message_targets(messages, sample_config)) == [(1234, messages[0])]


class TestRss:
    def test_rss_skipped_by_default(self, sample_config) -> None:
        messages = [msg(1, content=url(7), recipient="rss", subject="random feed item")]
        assert list(iter_pr_message_targets(messages, sample_config)) == []

    def test_rss_allowlisted_subject_kept(self, sample_config) -> None:
        messages = [msg(1, content=url(7), recipient="rss", subject="mathlib bors notifications")]
        assert list(iter_pr_message_targets(messages, sample_config)) == [(7, messages[0])]


class TestHelpers:
    def test_messages_for_pr_filters_and_dedups(self, sample_config) -> None:
        messages = [
            msg(1, content=url(100)),
            msg(2, content=url(200)),
            msg(3, content=f"{url(100)} mentioned again"),
        ]
        result = messages_for_pr(messages, 100, sample_config)
        assert [m["id"] for m in result] == [1, 3]

    def test_index_messages_by_pr(self, sample_config) -> None:
        messages = [
            msg(1, content=url(100)),
            msg(2, recipient="PR reviews", subject="#200: t"),
            msg(3, content=url(100)),
        ]
        index = index_messages_by_pr(messages, sample_config)
        assert set(index) == {100, 200}
        assert [m["id"] for m in index[100]] == [1, 3]
        assert [m["id"] for m in index[200]] == [2]
