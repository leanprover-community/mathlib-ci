"""A rate-limit-aware wrapper around the Zulip client.

Zulip rate-limits per bot user: every call draws from one shared per-minute bucket, and on
overflow the server returns HTTP 429 with ``{"code": "RATE_LIMIT_HIT", "retry-after": s}``.
Reacting to 429s alone means we sprint into the limit and then stall; instead we *pace*
proactively from the ``X-RateLimit-Remaining`` / ``X-RateLimit-Reset`` headers the server
returns on every response, gliding the remaining budget across the rest of the window.

The pacing policy lives in :class:`RateLimitPacer` (pure, fully injectable clock/sleep, so
it is unit-testable). :class:`PacedZulipClient` composes the pacer with a ``zulip.Client``,
capturing response headers by wrapping the client's requests session, and keeps the
``retry-after`` backoff underneath as the floor.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Optional


def _header(headers: Mapping[str, Any], name: str) -> Optional[str]:
    """Case-insensitive header lookup that tolerates plain dicts and CaseInsensitiveDict."""
    if headers is None:
        return None
    # requests' CaseInsensitiveDict handles exact-name .get directly.
    value = headers.get(name) if hasattr(headers, "get") else None
    if value is not None:
        return value
    lname = name.lower()
    for key, val in headers.items():
        if key.lower() == lname:
            return val
    return None


class RateLimitPacer:
    """Decides how long to sleep before the next Zulip call, from observed headers.

    State is just ``remaining`` (requests left in the current window) and ``reset_at`` (the
    Unix time the window resets), both learned from response headers. The policy:

      * plenty of budget (``remaining`` above ``spread_threshold``) -> don't sleep, so the
        fast single-PR event path keeps its low latency;
      * getting low (``floor`` < ``remaining`` <= ``spread_threshold``) -> spread the rest
        of the budget evenly over the rest of the window (``time_to_reset / remaining``),
        so a long write burst glides toward the reset instead of slamming the floor;
      * at/below ``floor`` (or exhausted) -> wait out the window.

    All sleeps are clamped to ``max_sleep`` as a safety valve.
    """

    def __init__(
        self,
        *,
        floor: int = 5,
        spread_threshold: int = 20,
        reset_buffer: float = 1.0,
        max_sleep: float = 120.0,
        time_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ) -> None:
        self.floor = floor
        self.spread_threshold = spread_threshold
        self.reset_buffer = reset_buffer
        self.max_sleep = max_sleep
        self._time = time_fn
        self._sleep = sleep_fn
        self._log = log
        self.remaining: Optional[int] = None
        self.reset_at: Optional[float] = None

    def observe_headers(self, headers: Mapping[str, Any]) -> None:
        """Update budget state from a response's rate-limit headers (best-effort)."""
        remaining = _header(headers, "X-RateLimit-Remaining")
        reset = _header(headers, "X-RateLimit-Reset")
        if remaining is not None:
            try:
                self.remaining = int(float(remaining))
            except (TypeError, ValueError):
                pass
        if reset is not None:
            try:
                self.reset_at = float(reset)
            except (TypeError, ValueError):
                pass

    def note_rate_limited(self, retry_after: float) -> None:
        """Record a 429 so the next :meth:`pace` waits out ``retry_after`` seconds."""
        self.remaining = 0
        self.reset_at = self._time() + max(0.0, float(retry_after))

    def _compute_sleep(self) -> float:
        """Seconds to sleep before the next call; 0 if no throttling is warranted."""
        if self.remaining is None or self.reset_at is None:
            return 0.0
        time_to_reset = self.reset_at - self._time()
        if time_to_reset <= 0:
            # The window has rolled over; our reading is stale. Clear it and don't sleep —
            # the next call will refresh from fresh headers.
            self.remaining = None
            return 0.0
        if self.remaining <= self.floor:
            return min(time_to_reset + self.reset_buffer, self.max_sleep)
        if self.remaining <= self.spread_threshold:
            return min(time_to_reset / self.remaining, self.max_sleep)
        return 0.0

    def pace(self) -> float:
        """Sleep if the policy calls for it; return the seconds slept (0 if none)."""
        sleep_for = self._compute_sleep()
        if sleep_for > 0:
            self._log(
                f"Pacing: {self.remaining} requests left, sleeping {sleep_for:.2f}s "
                f"to glide to the rate-limit reset."
            )
            self._sleep(sleep_for)
        return sleep_for


class PacedZulipClient:
    """Wraps a ``zulip.Client`` so every call is paced and retried on rate limits.

    Use :meth:`call`, passing a bound client method and an optional request dict, exactly
    like the old ``call_with_retry`` helper — it is a drop-in with pacing added.
    """

    def __init__(
        self,
        client: Any,
        *,
        pacer: Optional[RateLimitPacer] = None,
        max_retries: int = 3,
        log: Callable[[str], None] = print,
    ) -> None:
        self.client = client
        self.pacer = pacer or RateLimitPacer(log=log)
        self.max_retries = max_retries
        self._log = log
        self._last_headers: Mapping[str, Any] = {}
        self._install_header_capture()

    def _install_header_capture(self) -> None:
        """Wrap the client's requests session so we can read response headers."""
        client = self.client
        if client is None or not hasattr(client, "ensure_session"):
            return
        try:
            client.ensure_session()
            session = client.session
            original_request = session.request

            def capturing_request(*args: Any, **kwargs: Any):
                response = original_request(*args, **kwargs)
                # Stash headers from the most recent response for the pacer to read.
                self._last_headers = getattr(response, "headers", {}) or {}
                return response

            session.request = capturing_request
        except Exception as err:  # pragma: no cover - defensive; pacing is best-effort
            self._log(f"Warning: could not install header capture, pacing degraded: {err}")

    def call(self, api_fn: Callable[..., dict], request: Optional[dict] = None) -> dict:
        """Call a Zulip API method, pacing beforehand and retrying on rate limits."""
        result: dict = {}
        for attempt in range(self.max_retries + 1):
            self.pacer.pace()
            result = api_fn(request) if request is not None else api_fn()
            self.pacer.observe_headers(self._last_headers)
            if result.get("code") != "RATE_LIMIT_HIT":
                return result
            retry_after = result.get("retry-after", 30)
            if attempt < self.max_retries:
                self._log(
                    f"Rate limited; backing off {float(retry_after):.1f}s "
                    f"(retry {attempt + 1}/{self.max_retries})."
                )
                self.pacer.note_rate_limited(retry_after)
            else:
                self._log(f"Rate limited after {self.max_retries} retries; giving up on this call.")
        return result

    # Convenience wrappers: paced, retried versions of the methods the reconciler uses.

    def get_messages(self, request: dict) -> dict:
        return self.call(self.client.get_messages, request)

    def get_subscriptions(self, request: Optional[dict] = None) -> dict:
        return self.call(self.client.get_subscriptions, request)

    def add_reaction(self, request: dict) -> dict:
        return self.call(self.client.add_reaction, request)

    def remove_reaction(self, request: dict) -> dict:
        return self.call(self.client.remove_reaction, request)
