"""Command-line orchestration for emoji reconciliation.

Two modes, one reconcile core:

  * ``--pr N [N ...]`` (event path): for each PR, fetch its GitHub state, find the Zulip
    messages that reference it, and reconcile each.
  * ``--sweep`` (safety net): fetch a bounded batch of recent messages, index them by PR,
    batch-fetch those PRs' GitHub states, and reconcile each message.

The orchestration functions (:func:`reconcile_pr`, :func:`run_sweep`) take an already-built
paced client and a GraphQL runner, so they can be tested without any network. ``zulip`` is
imported lazily in :func:`build_client` so the rest of the package (and the test suite)
needs no Zulip dependency.

Emoji updates are cosmetic: a failure on one PR is logged and skipped, never fatal.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Callable, Iterable, Optional

from .config import Config, load_config
from .github_state import GraphQLRunner, fetch_pr_state, fetch_pr_states, gh_graphql_runner
from .messages import index_messages_by_pr, messages_for_pr
from .paced_client import PacedZulipClient, RateLimitPacer
from .pr_state import PrState, desired_emoji_set
from .reconcile import ReconcileResult, reconcile_message
from .zulip_io import fetch_recent_messages, search_pr_messages


def _reconcile_pr_messages(
    pr_state: PrState,
    messages: list[dict],
    config: Config,
    client: PacedZulipClient,
    *,
    dry_run: bool,
    log: Callable[[str], None],
) -> list[ReconcileResult]:
    """Reconcile every supplied message against one PR's desired emoji set."""
    desired = desired_emoji_set(pr_state, config)
    desired_names = ", ".join(rule.name for rule in desired) or "(none)"
    log(f"PR #{pr_state.number}: status={pr_state.status} ci={pr_state.ci} "
        f"-> desired: {desired_names} ({len(messages)} message(s))")
    return [
        reconcile_message(message, desired, config, client, dry_run=dry_run, log=log)
        for message in messages
    ]


def reconcile_pr(
    pr_number: int,
    config: Config,
    client: PacedZulipClient,
    runner: GraphQLRunner,
    *,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> list[ReconcileResult]:
    """Event path: reconcile all of one PR's Zulip messages to its current GitHub state."""
    pr_state = fetch_pr_state(config.github_repo, pr_number, config, runner)
    if pr_state is None:
        log(f"PR #{pr_number}: not found on GitHub; skipping")
        return []
    messages = search_pr_messages(client, pr_number, log=log)
    targets = messages_for_pr(messages, pr_number, config)
    return _reconcile_pr_messages(pr_state, targets, config, client, dry_run=dry_run, log=log)


def run_sweep(
    config: Config,
    client: PacedZulipClient,
    runner: GraphQLRunner,
    *,
    num_before: int,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> list[ReconcileResult]:
    """Sweep: reconcile every PR referenced by a bounded batch of recent messages."""
    messages = fetch_recent_messages(client, num_before=num_before, log=log)
    index = index_messages_by_pr(messages, config)
    log(f"Sweep: {len(messages)} message(s) reference {len(index)} PR(s)")
    if not index:
        return []

    states = fetch_pr_states(config.github_repo, index.keys(), config, runner)
    log(f"Sweep: fetched state for {len(states)}/{len(index)} PR(s)")

    results: list[ReconcileResult] = []
    for pr_number, pr_messages in sorted(index.items()):
        pr_state = states.get(pr_number)
        if pr_state is None:
            log(f"PR #{pr_number}: no GitHub state (deleted? not a PR?); skipping")
            continue
        results.extend(
            _reconcile_pr_messages(pr_state, pr_messages, config, client, dry_run=dry_run, log=log)
        )
    return results


def summarize(results: Iterable[ReconcileResult], log: Callable[[str], None] = print) -> None:
    """Log a one-line tally of what the run changed."""
    results = list(results)
    added = sum(len(r.added) for r in results)
    removed = sum(len(r.removed) for r in results)
    changed = sum(1 for r in results if r.changed)
    log(f"Done: {changed} message(s) changed ({added} added, {removed} removed) "
        f"across {len(results)} message(s) examined")


def build_client(
    api_key: str,
    email: str,
    site: str,
    *,
    pacer: Optional[RateLimitPacer] = None,
    log: Callable[[str], None] = print,
) -> PacedZulipClient:
    """Construct a paced Zulip client. Imports ``zulip`` lazily."""
    import zulip  # noqa: PLC0415 - lazy so the package has no hard Zulip dependency

    client = zulip.Client(email=email, api_key=api_key, site=site)
    return PacedZulipClient(client, pacer=pacer, log=log)


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
                        help="Recent messages to scan per channel in --sweep (default 5000).")
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

    results: list[ReconcileResult] = []
    if args.sweep:
        results = run_sweep(config, client, runner,
                            num_before=args.sweep_messages, dry_run=args.dry_run)
    else:
        for pr_number in args.pr:
            try:
                results.extend(reconcile_pr(pr_number, config, client, runner,
                                            dry_run=args.dry_run))
            except Exception as err:  # cosmetic: never let one PR fail the run
                print(f"PR #{pr_number}: error during reconcile (skipping): {err}",
                      file=sys.stderr)

    summarize(results)
    return 0
