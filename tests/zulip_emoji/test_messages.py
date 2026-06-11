"""Tests for message<->PR matching: URL links, pr_reviews threads, rss, dedup."""

from __future__ import annotations

from emoji_reconcile.messages import Target, index_targets, iter_pr_message_targets

REPO = "leanprover-community/mathlib4"


def msg(message_id, content="", recipient="general", subject=""):
    return {"id": message_id, "content": content, "display_recipient": recipient,
            "subject": subject, "reactions": []}


def url(n):
    return f"https://github.com/{REPO}/pull/{n}"


class TestUrlMatching:
    def test_link_in_body_matches(self, sample_config) -> None:
        messages = [msg(1, content=f"see {url(123)} please")]
        assert list(iter_pr_message_targets(messages, sample_config)) == [
            Target(123, messages[0], via="url")
        ]

    def test_full_number_not_prefix(self, sample_config) -> None:
        # A link to PR 1234 must not register as PR 123 (the old substring bug).
        messages = [msg(1, content=url(1234))]
        targets = list(iter_pr_message_targets(messages, sample_config))
        assert [t.pr_number for t in targets] == [1234]

    def test_multiple_links_one_message(self, sample_config) -> None:
        messages = [msg(1, content=f"{url(10)} and {url(20)}")]
        numbers = {t.pr_number for t in iter_pr_message_targets(messages, sample_config)}
        assert numbers == {10, 20}

    def test_duplicate_link_yields_once(self, sample_config) -> None:
        messages = [msg(1, content=f"{url(10)} {url(10)}")]
        assert [t.pr_number for t in iter_pr_message_targets(messages, sample_config)] == [10]

    def test_other_repo_link_ignored(self, sample_config) -> None:
        messages = [msg(1, content="https://github.com/other/repo/pull/5")]
        assert list(iter_pr_message_targets(messages, sample_config)) == []


class TestPrReviewsThread:
    def test_first_message_in_thread_matches_by_subject(self, sample_config) -> None:
        messages = [msg(1, recipient="PR reviews", subject="#123: a title")]
        assert list(iter_pr_message_targets(messages, sample_config)) == [
            Target(123, messages[0], via="topic")
        ]

    def test_only_first_message_in_thread(self, sample_config) -> None:
        messages = [
            msg(1, recipient="PR reviews", subject="#123: a title"),
            msg(2, recipient="PR reviews", subject="#123: a title"),
        ]
        targets = list(iter_pr_message_targets(messages, sample_config))
        # Only the opener (id 1) carries the reaction.
        assert [t.message["id"] for t in targets] == [1]

    def test_subject_match_only_in_pr_reviews(self, sample_config) -> None:
        # Same subject form but a different channel -> no subject-based match.
        messages = [msg(1, recipient="general", subject="#123: a title")]
        assert list(iter_pr_message_targets(messages, sample_config)) == []

    def test_subject_full_number(self, sample_config) -> None:
        messages = [msg(1, recipient="PR reviews", subject="#1234: t")]
        assert [t.pr_number for t in iter_pr_message_targets(messages, sample_config)] == [1234]

    def test_url_match_wins_over_topic_match(self, sample_config) -> None:
        # A pr_reviews opener that also links its PR is a url target (no confirmation needed).
        messages = [msg(1, content=url(123), recipient="PR reviews", subject="#123: t")]
        assert list(iter_pr_message_targets(messages, sample_config)) == [
            Target(123, messages[0], via="url")
        ]


class TestRss:
    def test_rss_skipped_by_default(self, sample_config) -> None:
        messages = [msg(1, content=url(7), recipient="rss", subject="random feed item")]
        assert list(iter_pr_message_targets(messages, sample_config)) == []

    def test_rss_allowlisted_subject_kept(self, sample_config) -> None:
        messages = [msg(1, content=url(7), recipient="rss", subject="mathlib bors notifications")]
        assert [t.pr_number for t in iter_pr_message_targets(messages, sample_config)] == [7]


class TestIndexTargets:
    def test_groups_by_pr(self, sample_config) -> None:
        messages = [
            msg(1, content=url(100)),
            msg(2, recipient="PR reviews", subject="#200: t"),
            msg(3, content=url(100)),
        ]
        index = index_targets(messages, sample_config)
        assert set(index) == {100, 200}
        assert [t.message["id"] for t in index[100]] == [1, 3]
        assert [t.message["id"] for t in index[200]] == [2]
        assert [t.via for t in index[200]] == ["topic"]
