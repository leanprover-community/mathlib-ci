"""Tests for RateLimitPacer pacing policy and PacedZulipClient retry/observe behavior."""

from __future__ import annotations

from emoji_reconcile.paced_client import PacedZulipClient, RateLimitPacer


class FakeClock:
    """A controllable monotonic clock; sleeping advances it and is recorded."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_pacer(clock: FakeClock, **kwargs) -> RateLimitPacer:
    return RateLimitPacer(
        time_fn=clock.time, sleep_fn=clock.sleep, log=lambda _m: None, **kwargs
    )


class TestPacerPolicy:
    def test_no_headers_no_sleep(self) -> None:
        clock = FakeClock()
        pacer = make_pacer(clock)
        assert pacer.pace() == 0.0
        assert clock.slept == []

    def test_plenty_of_budget_no_sleep(self) -> None:
        clock = FakeClock()
        pacer = make_pacer(clock, spread_threshold=20)
        pacer.observe_headers({"X-RateLimit-Remaining": "180", "X-RateLimit-Reset": str(clock.now + 50)})
        assert pacer.pace() == 0.0
        assert clock.slept == []

    def test_low_budget_spreads_over_window(self) -> None:
        clock = FakeClock()
        pacer = make_pacer(clock, floor=5, spread_threshold=20)
        # 10 requests left, 50s to reset -> ~5s spacing.
        pacer.observe_headers({"X-RateLimit-Remaining": "10", "X-RateLimit-Reset": str(clock.now + 50)})
        slept = pacer.pace()
        assert slept == 5.0
        assert clock.slept == [5.0]

    def test_at_floor_waits_out_window(self) -> None:
        clock = FakeClock()
        pacer = make_pacer(clock, floor=5, reset_buffer=1.0)
        pacer.observe_headers({"X-RateLimit-Remaining": "5", "X-RateLimit-Reset": str(clock.now + 30)})
        slept = pacer.pace()
        # waits time_to_reset + buffer
        assert slept == 31.0

    def test_exhausted_waits_out_window(self) -> None:
        clock = FakeClock()
        pacer = make_pacer(clock, reset_buffer=1.0)
        pacer.observe_headers({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(clock.now + 12)})
        assert pacer.pace() == 13.0

    def test_max_sleep_clamps(self) -> None:
        clock = FakeClock()
        pacer = make_pacer(clock, max_sleep=10.0)
        pacer.observe_headers({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(clock.now + 999)})
        assert pacer.pace() == 10.0

    def test_stale_window_clears_and_no_sleep(self) -> None:
        clock = FakeClock()
        pacer = make_pacer(clock)
        # reset is already in the past -> reading is stale.
        pacer.observe_headers({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(clock.now - 5)})
        assert pacer.pace() == 0.0
        assert pacer.remaining is None

    def test_note_rate_limited_forces_wait(self) -> None:
        clock = FakeClock()
        pacer = make_pacer(clock, reset_buffer=0.0)
        pacer.note_rate_limited(20)
        assert pacer.pace() == 20.0

    def test_case_insensitive_headers(self) -> None:
        clock = FakeClock()
        pacer = make_pacer(clock)
        pacer.observe_headers({"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(clock.now + 7)})
        assert pacer.remaining == 0
        assert pacer.pace() > 0


class TestPacedZulipClientRetry:
    def _client(self, clock: FakeClock) -> PacedZulipClient:
        # client=None skips header capture; we drive headers via _last_headers in api_fns.
        # reset_buffer=0 so a 429 backoff sleeps exactly retry-after for clean assertions.
        return PacedZulipClient(
            client=None, pacer=make_pacer(clock, reset_buffer=0.0),
            max_retries=3, log=lambda _m: None,
        )

    def test_success_passes_through(self) -> None:
        clock = FakeClock()
        pzc = self._client(clock)
        calls = []

        def api_fn(request):
            calls.append(request)
            pzc._last_headers = {"X-RateLimit-Remaining": "100", "X-RateLimit-Reset": str(clock.now + 60)}
            return {"result": "success", "messages": []}

        result = pzc.call(api_fn, {"anchor": "newest"})
        assert result["result"] == "success"
        assert calls == [{"anchor": "newest"}]

    def test_retries_then_succeeds(self) -> None:
        clock = FakeClock()
        pzc = self._client(clock)
        attempts = {"n": 0}

        def api_fn(request=None):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return {"result": "error", "code": "RATE_LIMIT_HIT", "retry-after": 3}
            pzc._last_headers = {"X-RateLimit-Remaining": "100", "X-RateLimit-Reset": str(clock.now + 60)}
            return {"result": "success"}

        result = pzc.call(api_fn)
        assert result["result"] == "success"
        assert attempts["n"] == 2
        # The 429 should have triggered a backoff sleep of ~retry-after.
        assert 3.0 in clock.slept

    def test_gives_up_after_max_retries(self) -> None:
        clock = FakeClock()
        pzc = self._client(clock)
        attempts = {"n": 0}

        def api_fn(request=None):
            attempts["n"] += 1
            return {"result": "error", "code": "RATE_LIMIT_HIT", "retry-after": 1}

        result = pzc.call(api_fn)
        assert result["code"] == "RATE_LIMIT_HIT"
        # initial try + 3 retries
        assert attempts["n"] == 4

    def test_no_request_arg_calls_without_argument(self) -> None:
        clock = FakeClock()
        pzc = self._client(clock)

        def api_fn():
            return {"result": "success", "subscriptions": []}

        assert pzc.call(api_fn)["result"] == "success"
