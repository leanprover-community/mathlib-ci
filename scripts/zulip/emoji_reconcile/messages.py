"""Decide which Zulip messages carry which PR's reactions.

A message is associated with a PR if either:
  * its body contains a link to that PR (``https://github.com/<repo>/pull/<n>``), or
  * it is the *first* message in a thread in the ``pr_reviews`` channel whose topic
    references ``#<n>`` (these threads are one-per-PR, and we react only on the opener).

Messages in the ``rss`` channel are skipped unless their topic is allow-listed
(``rss_allow``), matching the bors-notifications carve-out.

This is the same matching used by both entry points: the event path runs it over the
messages returned by a ``#<n>`` search and keeps the target PR; the sweep runs it over
recent messages and indexes every PR it finds. Keeping it pure (a generator over an
already-fetched message list) makes it straightforward to test and keeps the dedup of
"first message per thread" deterministic in the order messages are supplied (oldest first,
as Zulip returns them).

Note: unlike the original script — which used the substring patterns ``#123`` and
``pull/123`` and so could mis-associate ``#1234`` with PR 123 — this extracts the full
number, so ``#1234`` resolves only to PR 1234.
"""

from __future__ import annotations

import re
from typing import Iterator

from .config import Config

# `#<digits>` as a whole token: not preceded or followed by another digit.
_SUBJECT_PR_RE = re.compile(r"(?<!\d)#(\d+)(?!\d)")

RSS_CHANNEL = "rss"


def _url_pattern(repo: str) -> "re.Pattern[str]":
    # `\d+` is greedy, so it captures the full PR number — pull/1234 yields 1234, not 123.
    return re.compile(re.escape(f"https://github.com/{repo}/pull/") + r"(\d+)")


def iter_pr_message_targets(
    messages: list[dict], config: Config
) -> Iterator[tuple[int, dict]]:
    """Yield ``(pr_number, message)`` for every PR a message should carry reactions for.

    The same ``(pr_number, message)`` may be yielded at most once. A message can reference
    several PRs (multiple links), in which case it is yielded once per distinct PR.
    """
    url_re = _url_pattern(config.github_repo)
    pr_reviews = config.channel_name("pr_reviews")
    seen_thread_subjects: set[str] = set()

    for message in messages:
        recipient = message.get("display_recipient")
        subject = message.get("subject") or ""

        # Skip RSS noise except allow-listed topics (e.g. bors notifications).
        if recipient == RSS_CHANNEL and subject not in config.rss_allow:
            continue

        emitted: set[int] = set()

        # 1. Any PR links in the message body.
        content = message.get("content") or ""
        for match in url_re.finditer(content):
            number = int(match.group(1))
            if number not in emitted:
                emitted.add(number)
                yield number, message

        # 2. First message of a PR-reviews thread whose topic references a PR.
        if pr_reviews and recipient == pr_reviews and subject not in seen_thread_subjects:
            seen_thread_subjects.add(subject)
            subject_match = _SUBJECT_PR_RE.search(subject)
            if subject_match:
                number = int(subject_match.group(1))
                if number not in emitted:
                    emitted.add(number)
                    yield number, message


def messages_for_pr(messages: list[dict], pr_number: int, config: Config) -> list[dict]:
    """The messages that should carry PR ``pr_number``'s reactions (event-path filter)."""
    result: list[dict] = []
    seen_ids: set[int] = set()
    for number, message in iter_pr_message_targets(messages, config):
        if number != pr_number:
            continue
        mid = message.get("id")
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        result.append(message)
    return result


def index_messages_by_pr(messages: list[dict], config: Config) -> dict[int, list[dict]]:
    """Group messages by the PR they carry reactions for (sweep-path index)."""
    index: dict[int, list[dict]] = {}
    seen_pairs: set[tuple[int, int]] = set()
    for number, message in iter_pr_message_targets(messages, config):
        pair = (number, message.get("id"))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        index.setdefault(number, []).append(message)
    return index
