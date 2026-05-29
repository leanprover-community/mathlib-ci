"""Per-message reconciliation: make one Zulip message's reactions match the desired set.

Given a message, the desired state rules for its PR, and the config, this computes the diff
against the reactions currently on the message and applies it:

  * add desired emoji that aren't present;
  * remove managed emoji that are present but no longer desired;
  * never touch reactions outside the config's managed set (human 👍s are safe);
  * never remove a ``sticky`` emoji (e.g. the "migrated from a fork" marker);
  * skip emoji suppressed in this message's channel/topic (``suppress_in``).

Removals use the reaction's own ``emoji_code``/``reaction_type`` from the message, which is
more robust than re-deriving them from config (and is required for custom realm emoji).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

from .config import Config, StateRule


class Reactor(Protocol):
    """The slice of the Zulip client the reconciler needs (PacedZulipClient implements it)."""

    def add_reaction(self, request: dict) -> dict: ...
    def remove_reaction(self, request: dict) -> dict: ...


@dataclass
class ReconcileResult:
    """What a single-message reconcile did (or would do, under dry-run)."""

    message_id: int
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    suppressed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


def _is_suppressed(rule: StateRule, message: dict, config: Config) -> bool:
    """Whether ``rule``'s emoji should be skipped on this particular message."""
    if not rule.suppress_in:
        return False
    recipient = message.get("display_recipient")
    subject = (message.get("subject") or "").lower()
    for sup in rule.suppress_in:
        channel_name = config.channel_name(sup.channel)
        if channel_name is None or recipient != channel_name:
            continue
        if subject.startswith(sup.subject_prefix.lower()):
            return True
    return False


def _removal_request(message_id: int, reaction: dict) -> dict:
    """Build a remove_reaction request from a reaction object already on the message."""
    request: dict[str, Any] = {
        "message_id": message_id,
        "emoji_name": reaction["emoji_name"],
    }
    # Carry the custom-emoji identifiers through; required to remove realm emoji.
    if reaction.get("emoji_code") is not None:
        request["emoji_code"] = reaction["emoji_code"]
    if reaction.get("reaction_type") is not None:
        request["reaction_type"] = reaction["reaction_type"]
    return request


def reconcile_message(
    message: dict,
    desired_rules: Iterable[StateRule],
    config: Config,
    reactor: Reactor,
    *,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> ReconcileResult:
    """Diff one message's managed reactions against the desired set and apply the change."""
    message_id = message["id"]
    managed = config.managed_emojis
    sticky_emojis = {rule.emoji for rule in config.states if rule.sticky}

    # Only reactions we manage are eligible for removal; everything else is left alone.
    current = [rx for rx in message.get("reactions", []) if rx.get("emoji_name") in managed]
    present_emojis = {rx["emoji_name"] for rx in current}

    desired_rules = list(desired_rules)
    applicable = [r for r in desired_rules if not _is_suppressed(r, message, config)]
    suppressed = [r.emoji for r in desired_rules if _is_suppressed(r, message, config)]
    desired_emojis = {r.emoji for r in applicable}

    to_add = [r for r in applicable if r.emoji not in present_emojis]
    # Remove managed reactions that aren't desired and aren't sticky. De-dup by emoji name,
    # since a message can list the same emoji once per reacting user.
    to_remove: list[dict] = []
    seen_remove: set[str] = set()
    for rx in current:
        name = rx["emoji_name"]
        if name in desired_emojis or name in sticky_emojis or name in seen_remove:
            continue
        seen_remove.add(name)
        to_remove.append(rx)

    result = ReconcileResult(message_id=message_id, suppressed=suppressed)

    # Remove stale reactions first, then add new ones (mirrors the original ordering).
    for rx in to_remove:
        name = rx["emoji_name"]
        log(f"  - removing :{name}: from message {message_id}")
        if not dry_run:
            reactor.remove_reaction(_removal_request(message_id, rx))
        result.removed.append(name)

    for rule in to_add:
        log(f"  + adding :{rule.emoji}: to message {message_id}")
        if not dry_run:
            reactor.add_reaction(rule.reaction_request(message_id))
        result.added.append(rule.emoji)

    if not result.changed and not dry_run:
        log(f"  message {message_id} already up to date")
    return result
