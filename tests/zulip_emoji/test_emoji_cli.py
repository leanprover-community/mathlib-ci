"""Tests for CLI orchestration: reconcile_pr, run_sweep, arg parsing (no network)."""

from __future__ import annotations

import json

from emoji_reconcile.cli import main, reconcile_pr, run_sweep

from conftest import SAMPLE_CONFIG_DATA

REPO = "leanprover-community/mathlib4"


class FakeClient:
    """A retrying-client stand-in covering the reads + reaction writes the CLI uses."""

    def __init__(self, *, search_messages=None, recent_messages=None, subscriptions=None,
                 openers=None):
        self._search = search_messages or []
        self._recent = recent_messages or []
        self._subs = subscriptions or []
        # (channel, topic) -> id of the topic's true first message, for opener confirmation.
        self._openers = openers or {}
        self.added: list[dict] = []
        self.removed: list[dict] = []

    def get_subscriptions(self, request=None):
        return {"result": "success", "subscriptions": self._subs}

    def get_messages(self, request):
        narrow = {n["operator"]: n["operand"] for n in request["narrow"]}
        if "topic" in narrow:
            opener = self._openers.get((narrow["channel"], narrow["topic"]))
            messages = [{"id": opener}] if opener is not None else []
            return {"result": "success", "messages": messages}
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
        runner = lambda q: {"repository": {"pr123": graphql_node(123, labels=["awaiting-author"])}}
        results = reconcile_pr(123, sample_config, client, runner, log=lambda _x: None)
        assert client.added == [{"message_id": 1, "emoji_name": "writing"}]
        assert any(r.added == ["writing"] for r in results)

    def test_missing_pr_is_skipped(self, sample_config) -> None:
        # A NOT_FOUND alias comes back as null (or absent) -> skip, no Zulip writes.
        client = FakeClient()
        runner = lambda q: {"repository": {"pr99": None}}
        assert reconcile_pr(99, sample_config, client, runner, log=lambda _x: None) == []
        assert client.added == []

    def test_dry_run_does_not_mutate(self, sample_config) -> None:
        client = FakeClient(search_messages=[message(1, content=url(123))])
        runner = lambda q: {"repository": {"pr123": graphql_node(123, labels=["delegated"])}}
        results = reconcile_pr(123, sample_config, client, runner, dry_run=True, log=lambda _x: None)
        assert client.added == []
        assert any(r.added == ["peace_sign"] for r in results)

    def test_removes_stale_emoji(self, sample_config) -> None:
        # PR has no labels now, but a writing reaction lingers on the message.
        client = FakeClient(search_messages=[
            message(1, content=url(123), reactions=[{"emoji_name": "writing"}]),
        ])
        runner = lambda q: {"repository": {"pr123": graphql_node(123)}}
        reconcile_pr(123, sample_config, client, runner, log=lambda _x: None)
        assert client.removed == [{"message_id": 1, "emoji_name": "writing"}]

    def test_bot_user_id_scopes_removals(self, sample_config) -> None:
        # The stale reaction belongs to a human (user 7), not the bot (99): leave it.
        client = FakeClient(search_messages=[
            message(1, content=url(123), reactions=[{"emoji_name": "writing", "user_id": 7}]),
        ])
        runner = lambda q: {"repository": {"pr123": graphql_node(123)}}
        reconcile_pr(123, sample_config, client, runner, bot_user_id=99, log=lambda _x: None)
        assert client.removed == []


class TestReconcilePrUnion:
    def test_event_path_unions_co_referenced_prs(self, sample_config) -> None:
        # The event path for PR 123 finds a message that also links PR 456; it must fetch
        # PR 456's state too and reconcile the message against the combined set, not strip
        # PR 456's emoji.
        shared = message(7, content=f"{url(123)} {url(456)}",
                         reactions=[{"emoji_name": "peace_sign", "user_id": 99}])
        client = FakeClient(search_messages=[shared])

        def runner(query):
            assert "pr123:" in query and "pr456:" in query
            return {"repository": {
                "pr123": graphql_node(123, state="MERGED"),
                "pr456": graphql_node(456, labels=["delegated"]),
            }}

        reconcile_pr(123, sample_config, client, runner, bot_user_id=99,
                     log=lambda _x: None)
        assert client.removed == []
        assert {r["emoji_name"] for r in client.added} == {"merge"}


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

    def test_multi_pr_message_gets_union_not_last_writer(self, sample_config) -> None:
        # One message (a bors batch / digest) links a merged PR and an open labelled PR.
        # Reconciling it per PR would let each PR's pass remove the other's emoji; the
        # union must keep both and remove nothing.
        shared = message(1, content=f"{url(100)} and {url(200)}",
                         reactions=[{"emoji_name": "merge", "user_id": 99}])
        client = FakeClient(recent_messages=[shared])

        def runner(query):
            return {"repository": {
                "pr100": graphql_node(100, state="MERGED"),
                "pr200": graphql_node(200, labels=["delegated"]),
            }}

        results = run_sweep(sample_config, client, runner, num_before=100,
                            bot_user_id=99, log=lambda _x: None)
        assert client.removed == []  # PR 200's pass must not strip PR 100's merge emoji
        assert {r["emoji_name"] for r in client.added} == {"peace_sign"}
        assert len(results) == 1  # the shared message is reconciled exactly once

    def test_multi_pr_message_still_removes_union_stale(self, sample_config) -> None:
        # Emoji stale for BOTH PRs still goes: union semantics doesn't freeze cleanup.
        shared = message(1, content=f"{url(100)} and {url(200)}",
                         reactions=[{"emoji_name": "writing", "user_id": 99}])
        client = FakeClient(recent_messages=[shared])

        def runner(query):
            return {"repository": {
                "pr100": graphql_node(100, state="MERGED"),
                "pr200": graphql_node(200, labels=["delegated"]),
            }}

        run_sweep(sample_config, client, runner, num_before=100,
                  bot_user_id=99, log=lambda _x: None)
        assert [r["emoji_name"] for r in client.removed] == ["writing"]

    def test_pr_without_state_skipped(self, sample_config) -> None:
        client = FakeClient(recent_messages=[message(1, content=url(100))])
        run_sweep(sample_config, client, lambda q: {"repository": {}}, num_before=100,
                  log=lambda _x: None)
        assert client.added == []

    def test_empty_sweep_is_noop(self, sample_config) -> None:
        client = FakeClient(recent_messages=[])
        assert run_sweep(sample_config, client, lambda q: {}, num_before=100,
                         log=lambda _x: None) == []

    def test_confirmed_topic_opener_reconciled(self, sample_config) -> None:
        opener = message(1, recipient="PR reviews", subject="#100: t")
        client = FakeClient(recent_messages=[opener],
                            openers={("PR reviews", "#100: t"): 1})
        runner = lambda q: {"repository": {"pr100": graphql_node(100, labels=["awaiting-author"])}}
        run_sweep(sample_config, client, runner, num_before=100, log=lambda _x: None)
        assert client.added == [{"message_id": 1, "emoji_name": "writing"}]

    def test_mid_thread_message_not_treated_as_opener(self, sample_config) -> None:
        # The thread's true opener (id 1) predates the sweep window; the oldest in-window
        # message (id 5) must not get the reaction.
        candidate = message(5, recipient="PR reviews", subject="#100: t")
        client = FakeClient(recent_messages=[candidate],
                            openers={("PR reviews", "#100: t"): 1})
        runner = lambda q: {"repository": {"pr100": graphql_node(100, labels=["awaiting-author"])}}
        run_sweep(sample_config, client, runner, num_before=100, log=lambda _x: None)
        assert client.added == []

    def test_unconfirmable_opener_skipped(self, sample_config) -> None:
        # Opener lookup yields nothing -> conservative skip, no writes.
        candidate = message(5, recipient="PR reviews", subject="#100: t")
        client = FakeClient(recent_messages=[candidate])
        runner = lambda q: {"repository": {"pr100": graphql_node(100, labels=["awaiting-author"])}}
        run_sweep(sample_config, client, runner, num_before=100, log=lambda _x: None)
        assert client.added == []

    def test_one_pr_error_does_not_kill_sweep(self, sample_config) -> None:
        client = FakeClient(recent_messages=[
            message(1, content=url(100)),
            message(2, content=url(200)),
        ])
        real_add = client.add_reaction

        def flaky_add(request):
            if request["message_id"] == 1:
                raise RuntimeError("boom")
            return real_add(request)

        client.add_reaction = flaky_add

        def runner(query):
            return {"repository": {
                "pr100": graphql_node(100, labels=["awaiting-author"]),
                "pr200": graphql_node(200, labels=["delegated"]),
            }}

        run_sweep(sample_config, client, runner, num_before=100, log=lambda _x: None)
        # PR 100's failure is contained; PR 200 still reconciles.
        assert client.added == [{"message_id": 2, "emoji_name": "peace_sign"}]


class TestMainArgHandling:
    def test_sweep_window_defaults(self) -> None:
        from emoji_reconcile.cli import _parse_args
        args = _parse_args(["--config", "c.json", "--sweep"])
        assert args.sweep_messages == 2000
        assert args.sweep_private_messages == 1000

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
