"""Decide which Zulip messages carry which PR's reactions.

A message is associated with a PR if either:
  * its body contains a link to that PR (``https://github.com/<repo>/pull/<n>``), or
  * it is the *first* message in a thread in the ``pr_reviews`` channel whose topic
    references ``#<n>`` (these threads are one-per-PR, and we react only on the opener).

Each association is a :class:`Target` tagged with how it matched (``via``). "First message
in a thread" is judged within the supplied batch (oldest first, as Zulip returns them), so
a ``topic`` target whose true opener predates the batch can be wrong — the orchestration
layer confirms ``topic`` targets against Zulip before reacting (see ``cli``). ``url``
targets need no confirmation.

Messages in the rss channel (``channels.rss`` in the config, defaulting to ``rss``) are
skipped unless their topic is allow-listed (``rss_allow``), matching the bors-notifications
carve-out.

This is the same matching used by both entry points: the event path indexes the messages
returned by a ``#<n>`` search and keeps the target PR; the sweep indexes recent messages
and reconciles every PR it finds.

Note: unlike the original script — which used the substring patterns ``#123`` and
``pull/123`` and so could mis-associate ``#1234`` with PR 123 — this extracts the full
number, so ``#1234`` resolves only to PR 1234.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

from .config import Config

# `#<digits>` as a whole token: not preceded or followed by another digit.
_SUBJECT_PR_RE = re.compile(r"(?<!\d)#(\d+)(?!\d)")

DEFAULT_RSS_CHANNEL = "rss"


@dataclass(frozen=True)
class Target:
    """One (PR, message) association and how it was found."""

    pr_number: int
    message: dict
    via: str  # "url" (link in the body) or "topic" (pr_reviews thread opener)


def _url_pattern(repo: str) -> "re.Pattern[str]":
    # `\d+` is greedy, so it captures the full PR number — pull/1234 yields 1234, not 123.
    return re.compile(re.escape(f"https://github.com/{repo}/pull/") + r"(\d+)")


def iter_pr_message_targets(messages: list[dict], config: Config) -> Iterator[Target]:
    """Yield a :class:`Target` for every PR a message should carry reactions for.

    The same (PR, message) pair is yielded at most once; a message linking several PRs is
    yielded once per distinct PR. A message that matches both by URL and by topic counts
    as a ``url`` target (URL matches need no opener confirmation).
    """
    url_re = _url_pattern(config.github_repo)
    pr_reviews = config.channel_name("pr_reviews")
    rss_channel = config.channel_name("rss") or DEFAULT_RSS_CHANNEL
    seen_thread_subjects: set[str] = set()

    for message in messages:
        recipient = message.get("display_recipient")
        subject = message.get("subject") or ""

        # Skip RSS noise except allow-listed topics (e.g. bors notifications).
        if recipient == rss_channel and subject not in config.rss_allow:
            continue

        emitted: set[int] = set()

        # 1. Any PR links in the message body.
        content = message.get("content") or ""
        for match in url_re.finditer(content):
            number = int(match.group(1))
            if number not in emitted:
                emitted.add(number)
                yield Target(number, message, via="url")

        # 2. First message of a PR-reviews thread whose topic references a PR.
        if pr_reviews and recipient == pr_reviews and subject not in seen_thread_subjects:
            seen_thread_subjects.add(subject)
            subject_match = _SUBJECT_PR_RE.search(subject)
            if subject_match:
                number = int(subject_match.group(1))
                if number not in emitted:
                    emitted.add(number)
                    yield Target(number, message, via="topic")


def index_targets(messages: list[dict], config: Config) -> dict[int, list[Target]]:
    """Group targets by PR number. The event path takes its PR's entry; the sweep takes all."""
    index: dict[int, list[Target]] = {}
    for target in iter_pr_message_targets(messages, config):
        index.setdefault(target.pr_number, []).append(target)
    return index
