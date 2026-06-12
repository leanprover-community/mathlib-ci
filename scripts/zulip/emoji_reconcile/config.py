"""Per-repo configuration: schema, dataclasses, and a JSON loader/validator.

The config is owned by the *consuming* repo (e.g. mathlib4 ships its own ``config.json``)
so that mathlib-ci stays repo-agnostic. A config declares the GitHub repo, the Zulip
site/bot identity, the channel names the bot cares about, and a table of "state rules"
mapping PR state to emoji.

Each state rule says: "when this predicate holds for a PR, this emoji should be present."
Rules are grouped: within a ``group`` at most one emoji is shown at a time (the matching
rule with the highest ``priority`` wins); a rule with ``group: null`` is an independent
toggle driven solely by its own predicate. ``sticky`` rules are never removed once present
(e.g. the "migrated from a fork" marker). ``suppress_in`` skips an emoji on messages in a
particular channel/topic, where it would be redundant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Predicate kinds a rule's ``source`` may use.
SOURCE_KINDS = ("label", "state", "ci")
# Valid values for ``source.state`` and ``source.ci``.
VALID_STATES = ("open", "closed", "merged")
VALID_CI = ("running", "success", "failure")


class ConfigError(Exception):
    """Raised when a config file is malformed or internally inconsistent."""


@dataclass(frozen=True)
class Suppression:
    """A message context in which a rule's emoji should be skipped.

    ``channel`` is a *logical* channel key (a key of ``Config.channels``), not the raw
    Zulip channel name, so suppression travels with the config's channel mapping.
    """

    channel: str
    subject_prefix: str = ""


@dataclass(frozen=True)
class StateRule:
    """One emoji and the PR-state predicate that should make it present."""

    name: str
    emoji: str
    # Predicate: a kind from SOURCE_KINDS and the label name / state / CI value to match.
    source_kind: str
    source_value: str
    # Mutual-exclusion class; None means an independent toggle.
    group: str | None = None
    # Within a group, the matching rule with the largest priority wins (ties: config order).
    priority: int = 0
    # Custom (realm) emoji need both of these on add/remove requests; unicode emoji don't.
    emoji_code: str | None = None
    reaction_type: str | None = None
    # Never remove this reaction once present.
    sticky: bool = False
    # Contexts in which this emoji is redundant and should be skipped.
    suppress_in: tuple[Suppression, ...] = ()

    def matches(self, pr_state: Any) -> bool:
        """Whether this rule's predicate holds for ``pr_state`` (a ``PrState``)."""
        if self.source_kind == "label":
            return self.source_value in pr_state.labels
        if self.source_kind == "state":
            return pr_state.status == self.source_value
        return pr_state.ci == self.source_value  # "ci" (validation allows nothing else)

    def reaction_request(self, message_id: int) -> dict[str, Any]:
        """Build the kwargs for a Zulip add/remove-reaction call for this emoji."""
        request: dict[str, Any] = {"message_id": message_id, "emoji_name": self.emoji}
        if self.emoji_code is not None:
            request["emoji_code"] = self.emoji_code
        if self.reaction_type is not None:
            request["reaction_type"] = self.reaction_type
        return request


@dataclass(frozen=True)
class Config:
    """A fully parsed, validated per-repo configuration."""

    github_repo: str
    zulip_site: str
    zulip_email: str
    channels: dict[str, str]
    rss_allow: tuple[str, ...]
    states: tuple[StateRule, ...]
    # Names that select which checks feed the CI emoji, matched as case-insensitive
    # substrings of the check-run name, the workflow name, or the status context. Empty
    # means "consider every check on the head commit" (the aggregate rollup). Naming the
    # gating jobs/workflows here keeps the CI emoji from flipping on unrelated checks.
    ci_check_names: tuple[str, ...] = ()
    # All emoji names this config manages. Reconciliation only ever touches these, so
    # human-added reactions (👍 etc.) are never disturbed.
    managed_emojis: frozenset[str] = field(default_factory=frozenset)

    def channel_name(self, key: str) -> str | None:
        """Resolve a logical channel key (e.g. ``pr_reviews``) to its Zulip name."""
        return self.channels.get(key)


def _require(obj: dict[str, Any], key: str, where: str) -> Any:
    if key not in obj:
        raise ConfigError(f"{where}: missing required key '{key}'")
    return obj[key]


def _parse_suppress(raw: Any, rule_name: str) -> tuple[Suppression, ...]:
    if raw is None:
        return ()
    # Accept a single object or a list of them.
    items = raw if isinstance(raw, list) else [raw]
    result: list[Suppression] = []
    for item in items:
        if not isinstance(item, dict):
            raise ConfigError(f"state '{rule_name}': each suppress_in entry must be an object")
        channel = _require(item, "channel", f"state '{rule_name}' suppress_in")
        result.append(Suppression(channel=channel, subject_prefix=item.get("subject_prefix", "")))
    return tuple(result)


def _parse_rule(raw: dict[str, Any], known_channels: set[str]) -> StateRule:
    name = _require(raw, "name", "state rule")
    where = f"state '{name}'"
    emoji = _require(raw, "emoji", where)

    source = _require(raw, "source", where)
    if not isinstance(source, dict):
        raise ConfigError(f"{where}: 'source' must be an object")
    present = [k for k in SOURCE_KINDS if k in source]
    if len(present) != 1:
        raise ConfigError(
            f"{where}: 'source' must have exactly one of {SOURCE_KINDS}, got {present or 'none'}"
        )
    kind = present[0]
    value = source[kind]
    if kind == "state" and value not in VALID_STATES:
        raise ConfigError(f"{where}: source.state must be one of {VALID_STATES}, got '{value}'")
    if kind == "ci" and value not in VALID_CI:
        raise ConfigError(f"{where}: source.ci must be one of {VALID_CI}, got '{value}'")

    suppress = _parse_suppress(raw.get("suppress_in"), name)
    for sup in suppress:
        if sup.channel not in known_channels:
            raise ConfigError(
                f"{where}: suppress_in references unknown channel key '{sup.channel}'"
            )

    return StateRule(
        name=name,
        emoji=emoji,
        source_kind=kind,
        source_value=value,
        group=raw.get("group"),
        priority=int(raw.get("priority", 0)),
        emoji_code=raw.get("emoji_code"),
        reaction_type=raw.get("reaction_type"),
        sticky=bool(raw.get("sticky", False)),
        suppress_in=suppress,
    )


def parse_config(data: dict[str, Any]) -> Config:
    """Validate a config dict and return a :class:`Config`. Raises :class:`ConfigError`."""
    if not isinstance(data, dict):
        raise ConfigError("config root must be an object")

    github_repo = _require(data, "github_repo", "config")
    zulip = _require(data, "zulip", "config")
    if not isinstance(zulip, dict):
        raise ConfigError("config: 'zulip' must be an object")
    zulip_site = _require(zulip, "site", "config.zulip")
    zulip_email = _require(zulip, "email", "config.zulip")

    channels_raw = data.get("channels", {})
    if not isinstance(channels_raw, dict):
        raise ConfigError("config: 'channels' must be an object")
    # rss_allow is a list of raw channel names, kept separate from the logical-key map.
    rss_allow = tuple(channels_raw.get("rss_allow", []) or ())
    channels = {k: v for k, v in channels_raw.items() if k != "rss_allow"}
    known_channels = set(channels)

    states_raw = _require(data, "states", "config")
    if not isinstance(states_raw, list) or not states_raw:
        raise ConfigError("config: 'states' must be a non-empty list")

    rules = tuple(_parse_rule(r, known_channels) for r in states_raw)

    # A group is mutually exclusive only if its rules have distinct priorities or we accept
    # config-order tiebreaks; either way, names must be unique for sane logging/debugging.
    names = [r.name for r in rules]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ConfigError(f"config: duplicate state names: {sorted(dupes)}")

    ci_raw = data.get("ci", {})
    if not isinstance(ci_raw, dict):
        raise ConfigError("config: 'ci' must be an object")
    ci_check_names = tuple(ci_raw.get("check_names", []) or ())

    return Config(
        github_repo=github_repo,
        zulip_site=zulip_site,
        zulip_email=zulip_email,
        channels=channels,
        rss_allow=rss_allow,
        states=rules,
        ci_check_names=ci_check_names,
        managed_emojis=frozenset(r.emoji for r in rules),
    )


def load_config(path: str | Path) -> Config:
    """Load and validate a config from a JSON file."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise ConfigError(f"{path}: invalid JSON: {err}") from err
    return parse_config(data)
