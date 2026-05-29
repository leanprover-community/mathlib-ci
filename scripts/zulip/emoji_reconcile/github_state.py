"""Fetch a PR's current state from GitHub and reduce it to a ``PrState``.

The reconciler is only as good as the state it reconciles toward, so this module is the
single place that reads GitHub. Parsing (GraphQL response -> ``PrState``) is kept as pure
functions so it can be unit-tested exhaustively; the transport (shelling out to ``gh api
graphql``) is a thin, injectable layer.

CI status is derived from the check-run rollup on the PR's head commit rather than from
``workflow_run`` events, so a periodic sweep can self-heal a stuck CI emoji. If the config
names specific checks (``ci.check_names``), only those count; otherwise every check on the
head commit is considered.
"""

from __future__ import annotations

import json
import subprocess
from typing import Callable, Iterable, Optional

from .config import Config
from .pr_state import CI_NONE, PrState

# A GraphQLRunner takes a query string and returns the parsed ``data`` object.
GraphQLRunner = Callable[[str], dict]

# CI signal precedence: a still-running pipeline outranks a stale failure, which outranks a
# stale success. "none" means no relevant check was found.
_CI_PRECEDENCE = ("running", "failure", "success")

# Check-run conclusions that we treat as a hard failure. CANCELLED / SKIPPED / NEUTRAL /
# STALE are deliberately ignored (they neither pass nor fail the CI emoji), mirroring the
# old workflow's treatment of "cancelled" as "clear the running emoji."
_FAILURE_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED"}


def _classify_check_run(status: Optional[str], conclusion: Optional[str]) -> Optional[str]:
    """Map a GraphQL CheckRun (status, conclusion) to running/success/failure or None."""
    if status is not None and status.upper() != "COMPLETED":
        return "running"
    if conclusion is None:
        return None
    conclusion = conclusion.upper()
    if conclusion == "SUCCESS":
        return "success"
    if conclusion in _FAILURE_CONCLUSIONS:
        return "failure"
    return None  # cancelled / skipped / neutral / stale -> no signal


def _classify_status_context(state: Optional[str]) -> Optional[str]:
    """Map a legacy commit-status context state to running/success/failure or None."""
    if state is None:
        return None
    state = state.upper()
    if state in ("PENDING", "EXPECTED"):
        return "running"
    if state == "SUCCESS":
        return "success"
    if state in ("ERROR", "FAILURE"):
        return "failure"
    return None


def _reduce_ci_signals(signals: Iterable[Optional[str]]) -> str:
    """Combine per-check signals into one CI value using the precedence order."""
    present = {s for s in signals if s}
    for value in _CI_PRECEDENCE:
        if value in present:
            return value
    return CI_NONE


def ci_from_head_commit(commit: dict, check_names: tuple[str, ...]) -> str:
    """Derive the CI value from a head-commit node's checks and legacy statuses.

    ``commit`` is the GraphQL ``commit`` object carrying ``checkSuites`` and ``status``.
    When ``check_names`` is non-empty, a check counts only if its check-run name, its
    workflow name, or its status context is in that set.
    """
    wanted = {name.lower() for name in check_names}

    def included(*candidates: Optional[str]) -> bool:
        if not wanted:
            return True
        return any(c and c.lower() in wanted for c in candidates)

    signals: list[Optional[str]] = []

    for suite in (commit.get("checkSuites") or {}).get("nodes") or []:
        workflow = ((suite.get("workflowRun") or {}).get("workflow") or {}).get("name")
        for run in (suite.get("checkRuns") or {}).get("nodes") or []:
            if included(run.get("name"), workflow):
                signals.append(_classify_check_run(run.get("status"), run.get("conclusion")))

    for context in (commit.get("status") or {}).get("contexts") or []:
        if included(context.get("context")):
            signals.append(_classify_status_context(context.get("state")))

    return _reduce_ci_signals(signals)


def pr_state_from_node(node: dict, config: Config) -> PrState:
    """Reduce a GraphQL pullRequest node to a ``PrState``."""
    number = int(node["number"])
    raw_state = (node.get("state") or "OPEN").upper()
    status = {"OPEN": "open", "CLOSED": "closed", "MERGED": "merged"}.get(raw_state, "open")

    labels = [n["name"] for n in (node.get("labels") or {}).get("nodes") or []]

    ci = CI_NONE
    commit_nodes = (node.get("commits") or {}).get("nodes") or []
    if commit_nodes:
        commit = (commit_nodes[0] or {}).get("commit") or {}
        ci = ci_from_head_commit(commit, config.ci_check_names)

    return PrState.make(number=number, status=status, labels=labels, ci=ci)


# The set of fields fetched for each PR. Reused for single and batched queries.
_PR_FIELDS = """
  number
  state
  labels(first: 100) { nodes { name } }
  commits(last: 1) {
    nodes {
      commit {
        statusCheckRollup { state }
        status { contexts { context state } }
        checkSuites(first: 30) {
          nodes {
            workflowRun { workflow { name } }
            checkRuns(first: 100) { nodes { name status conclusion } }
          }
        }
      }
    }
  }
"""


def _split_repo(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise ValueError(f"github_repo must be 'owner/name', got '{repo}'")
    return owner, name


def build_single_query(repo: str, number: int) -> str:
    owner, name = _split_repo(repo)
    return f'''query {{
  repository(owner: "{owner}", name: "{name}") {{
    pullRequest(number: {int(number)}) {{
      {_PR_FIELDS}
    }}
  }}
}}'''


def build_batch_query(repo: str, numbers: Iterable[int]) -> str:
    """A single query fetching several PRs via aliased pullRequest fields."""
    owner, name = _split_repo(repo)
    aliases = "\n".join(
        f'    pr{int(n)}: pullRequest(number: {int(n)}) {{ {_PR_FIELDS} }}'
        for n in numbers
    )
    return f'''query {{
  repository(owner: "{owner}", name: "{name}") {{
{aliases}
  }}
}}'''


def gh_graphql_runner(query: str) -> dict:
    """Default transport: run ``gh api graphql`` and return the parsed ``data`` object.

    Relies on ``gh`` being installed and authenticated (``GH_TOKEN``/``GITHUB_TOKEN``),
    matching how the existing workflows call GitHub.
    """
    proc = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    if "errors" in payload and payload["errors"]:
        raise RuntimeError(f"GitHub GraphQL errors: {payload['errors']}")
    return payload.get("data") or {}


def fetch_pr_state(
    repo: str,
    number: int,
    config: Config,
    runner: GraphQLRunner = gh_graphql_runner,
) -> Optional[PrState]:
    """Fetch one PR's state. Returns None if the PR can't be found."""
    data = runner(build_single_query(repo, number))
    node = ((data.get("repository") or {}).get("pullRequest")) if data else None
    if not node:
        return None
    return pr_state_from_node(node, config)


def fetch_pr_states(
    repo: str,
    numbers: Iterable[int],
    config: Config,
    runner: GraphQLRunner = gh_graphql_runner,
    chunk_size: int = 50,
) -> dict[int, PrState]:
    """Fetch many PRs' states in batched GraphQL queries. Missing PRs are omitted."""
    numbers = sorted({int(n) for n in numbers})
    states: dict[int, PrState] = {}
    for start in range(0, len(numbers), chunk_size):
        chunk = numbers[start:start + chunk_size]
        data = runner(build_batch_query(repo, chunk))
        repository = (data.get("repository") or {}) if data else {}
        for n in chunk:
            node = repository.get(f"pr{n}")
            if node:
                states[n] = pr_state_from_node(node, config)
    return states
