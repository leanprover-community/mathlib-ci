"""Summarise a `lake build` log for Zulip and the GitHub job summary.

The entry point is `scripts/reporting/zulip_build_report.py`; this package holds the
implementation, split by responsibility:

* `lake_log`: the message model, the parser for Lake's text output, attribution of
  messages to linters, and the count aggregations both renderers share;
* `context`: the report context (repository, commit, run) and how it is read from the
  environment, with the same fallbacks as `zulip_build_report.sh`;
* `zulip`: the Zulip message, kept small enough to never hit Zulip's size cap;
* `summary`: the GitHub job summary, one row per message with a link to the source;
* `cli`: `main`, which wires the above together.
"""
