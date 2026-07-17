"""Tests for RetryingZulipClient: rate-limit retry behavior and bot-identity lookup."""

from __future__ import annotations

from emoji_reconcile.zulip_client import RetryingZulipClient


class FakeZulip:
    """Stands in for zulip.Client where only get_profile is needed."""

    def __init__(self, profile_response):
        self._profile = profile_response
        self.profile_calls = 0

    def get_profile(self):
        self.profile_calls += 1
        return self._profile


def make_client(zulip_client=None, **kwargs):
    slept: list[float] = []
    client = RetryingZulipClient(
        zulip_client, sleep_fn=slept.append, log=lambda _m: None, **kwargs
    )
    return client, slept


class TestRetry:
    def test_success_passes_through(self) -> None:
        client, slept = make_client()
        calls = []

        def api_fn(request):
            calls.append(request)
            return {"result": "success", "messages": []}

        assert client.call(api_fn, {"anchor": "newest"})["result"] == "success"
        assert calls == [{"anchor": "newest"}]
        assert slept == []

    def test_retries_with_retry_after_then_succeeds(self) -> None:
        client, slept = make_client()
        attempts = {"n": 0}

        def api_fn(request=None):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return {"result": "error", "code": "RATE_LIMIT_HIT", "retry-after": 3}
            return {"result": "success"}

        assert client.call(api_fn)["result"] == "success"
        assert attempts["n"] == 2
        assert slept == [3.0]

    def test_gives_up_after_max_retries(self) -> None:
        client, slept = make_client(max_retries=3)
        attempts = {"n": 0}

        def api_fn(request=None):
            attempts["n"] += 1
            return {"result": "error", "code": "RATE_LIMIT_HIT", "retry-after": 1}

        result = client.call(api_fn)
        assert result["code"] == "RATE_LIMIT_HIT"
        # initial try + 3 retries, sleeping before each retry but not after the last try.
        assert attempts["n"] == 4
        assert slept == [1.0, 1.0, 1.0]

    def test_non_rate_limit_error_not_retried(self) -> None:
        client, _slept = make_client()
        attempts = {"n": 0}

        def api_fn(request=None):
            attempts["n"] += 1
            return {"result": "error", "code": "BAD_REQUEST"}

        assert client.call(api_fn)["code"] == "BAD_REQUEST"
        assert attempts["n"] == 1

    def test_no_request_arg_calls_without_argument(self) -> None:
        client, _slept = make_client()

        def api_fn():
            return {"result": "success", "subscriptions": []}

        assert client.call(api_fn)["result"] == "success"


class TestUserId:
    def test_fetched_once_and_cached(self) -> None:
        zulip = FakeZulip({"result": "success", "user_id": 42, "email": "bot@example.com"})
        client, _slept = make_client(zulip)
        assert client.user_id() == 42
        assert client.user_id() == 42
        assert zulip.profile_calls == 1

    def test_failure_returns_none(self) -> None:
        zulip = FakeZulip({"result": "error", "msg": "nope"})
        client, _slept = make_client(zulip)
        assert client.user_id() is None
