"""Reconciliation engine for Zulip emoji reactions that mirror GitHub PR state.

This package uses a state-reconciliation model rather than a delta-based one: compute the
full *desired* set of emoji from a PR's current GitHub state, then make the Zulip message
match it (add what's missing, remove what's stale). Reconciliation is idempotent and
self-healing, so the same code path serves both fast per-PR event triggers and a periodic
sweep that catches dropped events.

Everything repo-specific lives in a JSON config (see ``config.py``); the engine itself is
repo-agnostic. See ``docs/zulip-emoji-reconcile.md`` for the design.
"""
