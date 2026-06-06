"""Zulip read helpers: fetch the messages the reconciler needs to look at.

Two access patterns, both built on :class:`PacedZulipClient` so every call is rate-limited
and retried:

  * :func:`search_pr_messages` — the event path. Search public channels (and each private
    channel the bot is subscribed to) for ``#<n>``, returning candidate messages for one
    PR. Zulip search can't span public and private channels at once, hence the per-channel
    private queries.
  * :func:`fetch_recent_messages` — the sweep. Pull the most recent messages from public
    channels and from each subscribed private channel, bounded by ``num_before``. This is
    the "invert the loop" strategy: fetch a bounded batch once, then index by PR, instead
    of issuing a search per open PR.

Both are deliberately defensive: a failed fetch logs a warning and is skipped rather than
aborting the run, because emoji updates are cosmetic and must never wedge.
"""

from __future__ import annotations

from typing import Callable

from .paced_client import PacedZulipClient

# Zulip caps a single get_messages page at 5000; we never ask for more in one call.
MAX_PAGE = 5000


def _private_channel_names(client: PacedZulipClient, log: Callable[[str], None]) -> list[str]:
    """Names of the invite-only channels the bot is subscribed to."""
    response = client.get_subscriptions()
    if response.get("result") != "success":
        log(f"Warning: failed to fetch subscriptions: {response}")
        return []
    return [
        sub["name"]
        for sub in response.get("subscriptions", [])
        if sub.get("invite_only", False)
    ]


def _get_messages(client: PacedZulipClient, narrow: list[dict], num_before: int) -> list[dict]:
    response = client.get_messages({
        "anchor": "newest",
        "num_before": min(num_before, MAX_PAGE),
        "num_after": 0,
        "narrow": narrow,
    })
    if response.get("result") != "success":
        return []
    return response.get("messages", [])


def _dedup_by_id(messages: list[dict]) -> list[dict]:
    seen: set[int] = set()
    unique: list[dict] = []
    for message in messages:
        mid = message.get("id")
        if mid in seen:
            continue
        seen.add(mid)
        unique.append(message)
    return unique


def search_pr_messages(
    client: PacedZulipClient,
    pr_number: int,
    *,
    num_before: int = MAX_PAGE,
    log: Callable[[str], None] = print,
) -> list[dict]:
    """Find candidate messages referencing ``#<pr_number>`` across public + private channels."""
    term = f"#{pr_number}"

    public = _get_messages(
        client,
        [{"operator": "channels", "operand": "public"}, {"operator": "search", "operand": term}],
        num_before,
    )
    log(f"Found {len(public)} candidate message(s) in public channels for {term}")
    messages = list(public)

    for channel in _private_channel_names(client, log):
        found = _get_messages(
            client,
            [{"operator": "channel", "operand": channel},
             {"operator": "search", "operand": term}],
            num_before,
        )
        if found:
            log(f"Found {len(found)} candidate message(s) in a private channel for {term}")
        messages.extend(found)

    return _dedup_by_id(messages)


def fetch_recent_messages(
    client: PacedZulipClient,
    *,
    num_before: int = MAX_PAGE,
    log: Callable[[str], None] = print,
) -> list[dict]:
    """Pull the most recent messages from public + subscribed private channels for the sweep.

    ``num_before`` bounds both the volume and, implicitly, the lookback window — the older
    end of the batch is the effective "recently closed" horizon.
    """
    public = _get_messages(client, [{"operator": "channels", "operand": "public"}], num_before)
    log(f"Fetched {len(public)} recent public message(s) for the sweep")
    messages = list(public)

    for channel in _private_channel_names(client, log):
        found = _get_messages(client, [{"operator": "channel", "operand": channel}], num_before)
        if found:
            log(f"Fetched {len(found)} recent message(s) from a private channel")
        messages.extend(found)

    return _dedup_by_id(messages)
