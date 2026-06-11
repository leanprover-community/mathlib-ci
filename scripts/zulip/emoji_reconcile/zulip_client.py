"""A retry-on-rate-limit wrapper around the Zulip client.

Zulip rate-limits per bot user and answers an over-limit request with HTTP 429 and a body
of ``{"code": "RATE_LIMIT_HIT", "retry-after": s}``. Sleeping out ``retry-after`` and
retrying is sufficient for this tool's traffic: the event path makes a handful of calls,
and the sweep is a cron job nobody is waiting on — the only burst is a first sweep (or one
after an outage), which simply glides through a few retry pauses.

The wrapper also knows the bot's own user id (fetched once, lazily), which the reconciler
uses to tell the bot's reactions apart from human ones.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional


class RetryingZulipClient:
    """Wraps a ``zulip.Client`` so every call is retried on rate limits.

    Use :meth:`call`, passing a bound client method and an optional request dict; the
    convenience wrappers below cover the methods the reconciler uses.
    """

    def __init__(
        self,
        client: Any,
        *,
        max_retries: int = 3,
        sleep_fn: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ) -> None:
        self.client = client
        self.max_retries = max_retries
        self._sleep = sleep_fn
        self._log = log
        self._user_id: Optional[int] = None

    def call(self, api_fn: Callable[..., dict], request: Optional[dict] = None) -> dict:
        """Call a Zulip API method, retrying on rate limits with the server's retry-after."""
        result: dict = {}
        for attempt in range(self.max_retries + 1):
            result = api_fn(request) if request is not None else api_fn()
            if result.get("code") != "RATE_LIMIT_HIT":
                return result
            retry_after = float(result.get("retry-after", 30))
            if attempt < self.max_retries:
                self._log(
                    f"Rate limited; sleeping {retry_after:.1f}s "
                    f"(retry {attempt + 1}/{self.max_retries})."
                )
                self._sleep(max(0.0, retry_after))
            else:
                self._log(f"Rate limited after {self.max_retries} retries; giving up on this call.")
        return result

    def user_id(self) -> Optional[int]:
        """The bot's own Zulip user id, fetched once via get_profile (None if unavailable)."""
        if self._user_id is None:
            response = self.call(self.client.get_profile)
            if response.get("result") == "success" and "user_id" in response:
                self._user_id = int(response["user_id"])
            else:
                self._log(f"Warning: could not fetch the bot's profile: {response}")
        return self._user_id

    # Convenience wrappers: retried versions of the methods the reconciler uses.

    def get_messages(self, request: dict) -> dict:
        return self.call(self.client.get_messages, request)

    def get_subscriptions(self, request: Optional[dict] = None) -> dict:
        return self.call(self.client.get_subscriptions, request)

    def add_reaction(self, request: dict) -> dict:
        return self.call(self.client.add_reaction, request)

    def remove_reaction(self, request: dict) -> dict:
        return self.call(self.client.remove_reaction, request)
