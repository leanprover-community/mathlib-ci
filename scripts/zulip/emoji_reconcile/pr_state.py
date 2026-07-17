"""The PR state model and the pure ``desired_emoji_set`` function.

``desired_emoji_set`` is the heart of the reconcile model: given a PR's current GitHub
state and a config, it returns exactly which managed emoji *should* be present, with no
reference to what's currently on the Zulip message. It is pure (no I/O), so it is cheap to
unit-test exhaustively and is the single source of truth for "what should this PR look
like."

Per-message concerns (``suppress_in``) are intentionally *not* handled here, because they
depend on the message, not the PR. They are applied later in ``reconcile_message``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config, StateRule

# Sentinel for "no CI signal" — distinct from running/success/failure so that a PR with no
# known CI conclusion has *no* CI emoji desired (and any stale CI emoji gets removed).
CI_NONE = "none"


@dataclass(frozen=True)
class PrState:
    """A PR's current GitHub state, reduced to what drives emoji reactions."""

    number: int
    # One of "open", "closed", "merged". "merged" is distinct from "closed": a merged PR is
    # also closed on GitHub, but we resolve it to "merged" before constructing PrState.
    status: str
    labels: frozenset[str] = field(default_factory=frozenset)
    # One of CI_NONE / "running" / "success" / "failure".
    ci: str = CI_NONE

    @classmethod
    def make(
        cls,
        number: int,
        status: str,
        labels: object = (),
        ci: str = CI_NONE,
    ) -> "PrState":
        """Convenience constructor that normalizes ``labels`` into a frozenset."""
        return cls(number=number, status=status, labels=frozenset(labels), ci=ci)


def desired_emoji_set(pr_state: PrState, config: Config) -> tuple[StateRule, ...]:
    """Return the state rules whose emoji should be present for ``pr_state``.

    Resolution:
      * Collect every rule whose predicate matches the PR state.
      * Within each named group, keep only the winner: highest ``priority``, ties broken by
        config order (earlier rule wins).
      * Rules with ``group is None`` are independent toggles and are all kept if matched.

    The result is ordered by config declaration order for stable, readable logging.
    """
    matched = [rule for rule in config.states if rule.matches(pr_state)]

    # Pick at most one winner per group; keep all groupless (independent) rules.
    group_winner: dict[str, StateRule] = {}
    independent: list[StateRule] = []
    for rule in matched:
        if rule.group is None:
            independent.append(rule)
            continue
        current = group_winner.get(rule.group)
        # Strictly-greater keeps the earlier rule on ties, honoring config order.
        if current is None or rule.priority > current.priority:
            group_winner[rule.group] = rule

    winners = set(group_winner.values()) | set(independent)
    # Return in config order for determinism.
    return tuple(rule for rule in config.states if rule in winners)
