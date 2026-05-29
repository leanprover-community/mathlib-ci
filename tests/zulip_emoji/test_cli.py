"""Tests for CLI orchestration: reconcile_pr, run_sweep, arg parsing (no network)."""

from __future__ import annotations

import json

from emoji_reconcile.cli import main, reconcile_pr, run_sweep

from conftest import SAMPLE_CONFIG_DATA

REPO = "leanprover-community/mathlib4"


class FakeClient:
    """A paced-client stand-in covering the reads + reaction writes the CLI uses."""

    def __init__(self, *, search_messages=None, recent_messages=None, subscriptions=None):
        self._search = search_messages or []
        self._recent = recent_messages or []
        self._subs = subscriptions or []
        self.added: list[dict] = []
        self.removed: list[dict] = []

    def get_subscriptions(self, request=None):
        return {"result": "success", "subscriptions": self._subs}

    def get_messages(self, request):
        narrow = {n["operator"]: n["operand"] for n in request["narrow"]}
        if "search" in narrow:
            return {"result": "success", "messages": self._search}
        return {"result": "success", "messages": self._recent}

    def add_reaction(self, request):
        self.added.append(request)
        return {"result": "success"}

    def remove_reaction(self, request):
        self.removed.append(request)
        return {"result": "success"}


def url(n):
    return f"https://github.com/{REPO}/pull/{n}"


def message(message_id, content="", recipient="general", subject="", reactions=None):
    return {"id": message_id, "content": content, "display_recipient": recipient,
            "subject": subject, "reactions": reactions or []}


def graphql_node(number, state="OPEN", labels=()):
    return {
        "number": number, "state": state,
        "labels": {"nodes": [{"name": n} for n in labels]},
        "commits": {"nodes": []},
    }


class TestReconcilePr:
    def test_adds_emoji_for_labelled_pr(self, sample_config) -> None:
        client = FakeClient(search_messages=[message(1, content=url(123))])
        runner = lambda q: {"repository": {"pullRequest": graphql_node(123, labels=["awaiting-author"])}}
        results = reconcile_pr(123, sample_config, client, runner, log=lambda _x: None)
        assert client.added == [{"message_id": 1, "emoji_name": "writing"}]
        assert any(r.added == ["writing"] for r in results)

    def test_missing_pr_is_skipped(self, sample_config) -> None:
        client = FakeClient()
        runner = lambda q: {"repository": {"pullRequest": None}}
        assert reconcile_pr(99, sample_config, client, runner, log=lambda _x: None) == []
        assert client.added == []

    def test_dry_run_does_not_mutate(self, sample_config) -> None:
        client = FakeClient(search_messages=[message(1, content=url(123))])
        runner = lambda q: {"repository": {"pullRequest": graphql_node(123, labels=["delegated"])}}
        results = reconcile_pr(123, sample_config, client, runner, dry_run=True, log=lambda _x: None)
        assert client.added == []
        assert any(r.added == ["peace_sign"] for r in results)

    def test_removes_stale_emoji(self, sample_config) -> None:
        # PR has no labels now, but a writing reaction lingers on the message.
        client = FakeClient(search_messages=[
            message(1, content=url(123), reactions=[{"emoji_name": "writing"}]),
        ])
        runner = lambda q: {"repository": {"pullRequest": graphql_node(123)}}
        reconcile_pr(123, sample_config, client, runner, log=lambda _x: None)
        assert client.removed == [{"message_id": 1, "emoji_name": "writing"}]


class TestRunSweep:
    def test_indexes_and_reconciles(self, sample_config) -> None:
        client = FakeClient(recent_messages=[
            message(1, content=url(100)),
            message(2, content=url(200)),
        ])

        def runner(query):
            repo = {}
            for n in (100, 200):
                if f"pr{n}:" in query:
                    labels = ["awaiting-author"] if n == 100 else ["delegated"]
                    repo[f"pr{n}"] = graphql_node(n, labels=labels)
            return {"repository": repo}

        run_sweep(sample_config, client, runner, num_before=100, log=lambda _x: None)
        added = {r["emoji_name"] for r in client.added}
        assert added == {"writing", "peace_sign"}

    def test_pr_without_state_skipped(self, sample_config) -> None:
        client = FakeClient(recent_messages=[message(1, content=url(100))])
        run_sweep(sample_config, client, lambda q: {"repository": {}}, num_before=100,
                  log=lambda _x: None)
        assert client.added == []

    def test_empty_sweep_is_noop(self, sample_config) -> None:
        client = FakeClient(recent_messages=[])
        assert run_sweep(sample_config, client, lambda q: {}, num_before=100,
                         log=lambda _x: None) == []


class TestMainArgHandling:
    def test_missing_api_key_returns_2(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("ZULIP_API_KEY", raising=False)
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(SAMPLE_CONFIG_DATA))
        # No key available and none passed -> exit code 2, before any Zulip import.
        assert main(["--config", str(config_path), "--pr", "1"]) == 2

    def test_requires_a_mode(self, tmp_path) -> None:
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(SAMPLE_CONFIG_DATA))
        # argparse exits (SystemExit) when neither --pr nor --sweep is given.
        try:
            main(["--config", str(config_path)])
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert exc.code != 0
