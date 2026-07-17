"""Tests for the Zulip read helpers using a fake retrying client."""

from __future__ import annotations

from emoji_reconcile.zulip_io import (
    fetch_recent_messages,
    first_message_id_in_topic,
    search_pr_messages,
)


class FakeClient:
    """Stands in for RetryingZulipClient: serves canned get_messages/get_subscriptions."""

    def __init__(self, *, subscriptions=None, by_narrow=None, recent=None, topics=None):
        self._subs = subscriptions or []
        self._by_narrow = by_narrow or {}  # key -> list of messages
        self._recent = recent or {}        # channel operand -> list of messages
        self._topics = topics or {}        # (channel, topic) -> list of messages, oldest first
        self.get_messages_calls = []

    def get_subscriptions(self, request=None):
        return {"result": "success", "subscriptions": self._subs}

    def get_messages(self, request):
        self.get_messages_calls.append(request)
        narrow = request["narrow"]
        ops = {n["operator"]: n["operand"] for n in narrow}
        if "topic" in ops:
            key = (ops.get("channel"), ops["topic"])
            return {"result": "success", "messages": self._topics.get(key, [])}
        if "search" in ops:
            key = (ops.get("channels") or ops.get("channel"), ops["search"])
            return {"result": "success", "messages": self._by_narrow.get(key, [])}
        # No search term -> "recent messages" mode, keyed by channel(s) operand.
        key = ops.get("channels") or ops.get("channel")
        return {"result": "success", "messages": self._recent.get(key, [])}


def m(message_id):
    return {"id": message_id, "reactions": []}


class TestSearchPrMessages:
    def test_public_and_private_combined(self) -> None:
        client = FakeClient(
            subscriptions=[
                {"name": "secret", "invite_only": True},
                {"name": "general", "invite_only": False},
            ],
            by_narrow={
                ("public", "#123"): [m(1), m(2)],
                ("secret", "#123"): [m(3)],
            },
        )
        result = search_pr_messages(client, 123, log=lambda _x: None)
        assert sorted(msg["id"] for msg in result) == [1, 2, 3]

    def test_only_invite_only_channels_searched_privately(self) -> None:
        client = FakeClient(
            subscriptions=[{"name": "general", "invite_only": False}],
            by_narrow={("public", "#5"): [m(1)]},
        )
        search_pr_messages(client, 5, log=lambda _x: None)
        # public search + no private search (general is not invite_only)
        searched_channels = [
            {n["operator"]: n["operand"] for n in c["narrow"]}.get("channel")
            for c in client.get_messages_calls
        ]
        assert searched_channels == [None]  # only the public (channels) query ran

    def test_dedup_across_channels(self) -> None:
        client = FakeClient(
            subscriptions=[{"name": "secret", "invite_only": True}],
            by_narrow={("public", "#9"): [m(1)], ("secret", "#9"): [m(1)]},
        )
        result = search_pr_messages(client, 9, log=lambda _x: None)
        assert [msg["id"] for msg in result] == [1]


class TestFetchRecentMessages:
    def test_combines_public_and_private(self) -> None:
        client = FakeClient(
            subscriptions=[{"name": "secret", "invite_only": True}],
            recent={"public": [m(1), m(2)], "secret": [m(3)]},
        )
        result = fetch_recent_messages(client, log=lambda _x: None)
        assert sorted(msg["id"] for msg in result) == [1, 2, 3]

    def test_respects_num_before_cap(self) -> None:
        client = FakeClient(recent={"public": [m(1)]})
        fetch_recent_messages(client, num_before=999999, log=lambda _x: None)
        # capped at MAX_PAGE
        assert client.get_messages_calls[0]["num_before"] == 5000


class TestFirstMessageIdInTopic:
    def test_returns_oldest_id(self) -> None:
        client = FakeClient(topics={("PR reviews", "#7: t"): [m(3), m(9)]})
        assert first_message_id_in_topic(client, "PR reviews", "#7: t") == 3

    def test_empty_topic_returns_none(self) -> None:
        client = FakeClient()
        assert first_message_id_in_topic(client, "PR reviews", "#7: t") is None

    def test_anchors_at_oldest(self) -> None:
        client = FakeClient(topics={("c", "t"): [m(1)]})
        first_message_id_in_topic(client, "c", "t")
        request = client.get_messages_calls[0]
        assert request["anchor"] == "oldest"
        assert request["num_before"] == 0
