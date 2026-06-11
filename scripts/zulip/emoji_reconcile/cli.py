"""Command-line orchestration for emoji reconciliation.

Two modes, one reconcile core:

  * ``--pr N [N ...]`` (event path): for each PR, fetch its GitHub state, find the Zulip
    messages that reference it, and reconcile each.
  * ``--sweep`` (safety net): fetch a bounded batch of recent messages, index them by PR,
    batch-fetch those PRs' GitHub states, and reconcile each message.

Targets matched by thread topic (rather than a PR link in the body) are confirmed against
Zulip before reacting: batch order alone can claim a mid-thread message is the "opener"
when the true opener predates the fetched window. Confirmations are cached per topic.

The orchestration functions (:func:`reconcile_pr`, :func:`run_sweep`) take an already-built
retrying client and a GraphQL runner, so they can be tested without any network. ``zulip``
is imported lazily in :func:`build_client` so the rest of the package (and the test suite)
needs no Zulip dependency.

Emoji updates are cosmetic: a failure on one PR is logged and skipped, never fatal.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Callable, Iterable, Optional

from .config import Config, load_config
from .github_state import GraphQLRunner, fetch_pr_states, gh_graphql_runner
from .messages import Target, index_targets
from .pr_state import PrState, desired_emoji_set
from .reconcile import ReconcileResult, reconcile_message
from .zulip_client import RetryingZulipClient
from .zulip_io import fetch_recent_messages, first_message_id_in_topic, search_pr_messages

# Cache for thread-opener lookups, keyed by (channel, topic) -> oldest message id (or None
# when the lookup failed). One run shares a single cache across all PRs.
OpenerCache = dict


def _confirmed_targets(
    targets: list[Target],
    client: RetryingZulipClient,
    opener_cache: OpenerCache,
    log: Callable[[str], None],
) -> list[Target]:
    """Drop ``topic`` targets that aren't really the first message of their topic."""
    confirmed: list[Target] = []
    for target in targets:
        if target.via != "topic":
            confirmed.append(target)
            continue
        message = target.message
        key = (message.get("display_recipient"), message.get("subject") or "")
        if key not in opener_cache:
            opener_cache[key] = first_message_id_in_topic(client, key[0], key[1])
        opener_id = opener_cache[key]
        if opener_id == message.get("id"):
            confirmed.append(target)
        elif opener_id is None:
            log(f"  message {message.get('id')}: could not confirm it opens '{key[1]}'; skipping")
        else:
            log(f"  message {message.get('id')} is not the opener of '{key[1]}' "
                f"(message {opener_id} is); skipping")
    return confirmed


def _reconcile_pr_targets(
    pr_state: PrState,
    targets: list[Target],
    config: Config,
    client: RetryingZulipClient,
    *,
    bot_user_id: Optional[int],
    dry_run: bool,
    log: Callable[[str], None],
) -> list[ReconcileResult]:
    """Reconcile every supplied message against one PR's desired emoji set."""
    desired = desired_emoji_set(pr_state, config)
    desired_names = ", ".join(rule.name for rule in desired) or "(none)"
    log(f"PR #{pr_state.number}: status={pr_state.status} ci={pr_state.ci} "
        f"-> desired: {desired_names} ({len(targets)} message(s))")
    return [
        reconcile_message(target.message, desired, config, client,
                          bot_user_id=bot_user_id, dry_run=dry_run, log=log)
        for target in targets
    ]


def reconcile_pr(
    pr_number: int,
    config: Config,
    client: RetryingZulipClient,
    runner: GraphQLRunner,
    *,
    bot_user_id: Optional[int] = None,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> list[ReconcileResult]:
    """Event path: reconcile all of one PR's Zulip messages to its current GitHub state."""
    pr_state = fetch_pr_states(config.github_repo, [pr_number], config, runner).get(pr_number)
    if pr_state is None:
        log(f"PR #{pr_number}: not found on GitHub; skipping")
        return []
    messages = search_pr_messages(client, pr_number, log=log)
    targets = index_targets(messages, config).get(pr_number, [])
    targets = _confirmed_targets(targets, client, {}, log)
    return _reconcile_pr_targets(pr_state, targets, config, client,
                                 bot_user_id=bot_user_id, dry_run=dry_run, log=log)


def run_sweep(
    config: Config,
    client: RetryingZulipClient,
    runner: GraphQLRunner,
    *,
    num_before: int,
    bot_user_id: Optional[int] = None,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> list[ReconcileResult]:
    """Sweep: reconcile every PR referenced by a bounded batch of recent messages."""
    messages = fetch_recent_messages(client, num_before=num_before, log=log)
    index = index_targets(messages, config)
    total = sum(len(targets) for targets in index.values())
    log(f"Sweep: {total} candidate message(s) reference {len(index)} PR(s)")
    if not index:
        return []

    states = fetch_pr_states(config.github_repo, index.keys(), config, runner)
    log(f"Sweep: fetched state for {len(states)}/{len(index)} PR(s)")

    opener_cache: OpenerCache = {}
    results: list[ReconcileResult] = []
    for pr_number, targets in sorted(index.items()):
        pr_state = states.get(pr_number)
        if pr_state is None:
            log(f"PR #{pr_number}: no GitHub state (deleted? not a PR?); skipping")
            continue
        try:
            targets = _confirmed_targets(targets, client, opener_cache, log)
            results.extend(
                _reconcile_pr_targets(pr_state, targets, config, client,
                                      bot_user_id=bot_user_id, dry_run=dry_run, log=log)
            )
        except Exception as err:  # cosmetic: never let one PR wedge the sweep
            log(f"PR #{pr_number}: error during reconcile (skipping): {err}")
    return results


def summarize(results: Iterable[ReconcileResult], log: Callable[[str], None] = print) -> None:
    """Log a one-line tally of what the run changed."""
    results = list(results)
    added = sum(len(r.added) for r in results)
    removed = sum(len(r.removed) for r in results)
    failed = sum(len(r.failed) for r in results)
    changed = sum(1 for r in results if r.changed)
    line = (f"Done: {changed} message(s) changed ({added} added, {removed} removed) "
            f"across {len(results)} message(s) examined")
    if failed:
        line += f"; {failed} reaction update(s) FAILED (see log above)"
    log(line)


def build_client(
    api_key: str,
    email: str,
    site: str,
    *,
    log: Callable[[str], None] = print,
) -> RetryingZulipClient:
    """Construct a retrying Zulip client. Imports ``zulip`` lazily."""
    import zulip  # noqa: PLC0415 - lazy so the package has no hard Zulip dependency

    client = zulip.Client(email=email, api_key=api_key, site=site)
    return RetryingZulipClient(client, log=log)


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile Zulip emoji reactions to current GitHub PR state."
    )
    parser.add_argument("--config", required=True, help="Path to the per-repo config JSON.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pr", type=int, nargs="+", metavar="N",
                      help="Reconcile these PR number(s) (event path).")
    mode.add_argument("--sweep", action="store_true",
                      help="Reconcile every PR referenced by recent Zulip messages.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log the planned changes without mutating Zulip.")
    parser.add_argument("--sweep-messages", type=int, default=5000,
                        help="Recent messages to scan in --sweep: one combined window "
                             "across all public channels, plus this many per private "
                             "channel (default 5000).")
    parser.add_argument("--zulip-api-key", default=os.environ.get("ZULIP_API_KEY"),
                        help="Zulip bot API key (default: $ZULIP_API_KEY).")
    parser.add_argument("--zulip-email", default=None,
                        help="Override the bot email from config.")
    parser.add_argument("--zulip-site", default=None,
                        help="Override the Zulip site from config.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    config = load_config(args.config)

    if not args.zulip_api_key:
        print("Error: no Zulip API key (pass --zulip-api-key or set ZULIP_API_KEY).",
              file=sys.stderr)
        return 2

    email = args.zulip_email or config.zulip_email
    site = args.zulip_site or config.zulip_site

    if args.dry_run:
        print("DRY RUN: no reactions will be added or removed.")

    client = build_client(args.zulip_api_key, email, site)
    runner = gh_graphql_runner

    bot_user_id = client.user_id()
    if bot_user_id is None:
        print("Warning: could not determine the bot's user id; "
              "treating every reaction as the bot's own.")

    results: list[ReconcileResult] = []
    if args.sweep:
        results = run_sweep(config, client, runner, num_before=args.sweep_messages,
                            bot_user_id=bot_user_id, dry_run=args.dry_run)
    else:
        for pr_number in args.pr:
            try:
                results.extend(reconcile_pr(pr_number, config, client, runner,
                                            bot_user_id=bot_user_id, dry_run=args.dry_run))
            except Exception as err:  # cosmetic: never let one PR fail the run
                print(f"PR #{pr_number}: error during reconcile (skipping): {err}",
                      file=sys.stderr)

    summarize(results)
    return 0
