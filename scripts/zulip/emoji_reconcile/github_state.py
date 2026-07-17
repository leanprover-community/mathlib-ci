"""Fetch a PR's current state from GitHub and reduce it to a ``PrState``.

The reconciler is only as good as the state it reconciles toward, so this module is the
single place that reads GitHub. Parsing (GraphQL response -> ``PrState``) is kept as pure
functions so it can be unit-tested exhaustively; the transport (shelling out to ``gh api
graphql``) is a thin, injectable layer.

CI status is derived from the check-run rollup on the PR's head commit rather than from
``workflow_run`` events, so a periodic sweep can self-heal a stuck CI emoji. If the config
names specific checks (``ci.check_names``, matched as case-insensitive substrings), only
those count; otherwise every check on the head commit is considered.
"""

from __future__ import annotations

import json
import subprocess
from typing import Callable, Iterable, Optional

from .config import Config
from .pr_state import CI_NONE, PrState

# A GraphQLRunner takes a query string and returns the parsed ``data`` object.
GraphQLRunner = Callable[[str], dict]

# CI signal precedence: a failure surfaces immediately, even while other checks are still
# running; running outranks success, so green shows only once everything relevant has
# finished. "none" means no relevant check was found.
_CI_PRECEDENCE = ("failure", "running", "success")

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
    When ``check_names`` is non-empty, a check counts only if one of those names is a
    case-insensitive substring of its check-run name, its workflow name, or its status
    context. Substring (rather than exact) matching lets a config name a job inside a
    reusable workflow without hardcoding the caller job's prefix: ``"Build"`` matches the
    check runs ``ci / Build`` and ``ci (fork) / Build``.
    """
    wanted = [name.lower() for name in check_names]

    def included(*candidates: Optional[str]) -> bool:
        if not wanted:
            return True
        return any(c and w in c.lower() for c in candidates for w in wanted)

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

    # A merge bot (e.g. bors) that rebases a PR's commits onto the base branch gives them new
    # SHAs, so GitHub never sees the PR head land and reports the PR as CLOSED rather than
    # MERGED. Such a bot renames the title to a fixed prefix on merge; treat a closed PR whose
    # title carries that prefix as merged. Native merges (merge queue / merge button) already
    # report MERGED above and never reach this branch.
    if status == "closed" and config.merged_title_prefix:
        if (node.get("title") or "").startswith(config.merged_title_prefix):
            status = "merged"

    labels = [n["name"] for n in (node.get("labels") or {}).get("nodes") or []]

    ci = CI_NONE
    commit_nodes = (node.get("commits") or {}).get("nodes") or []
    if commit_nodes:
        commit = (commit_nodes[0] or {}).get("commit") or {}
        ci = ci_from_head_commit(commit, config.ci_check_names)

    return PrState.make(number=number, status=status, labels=labels, ci=ci)


# The set of fields fetched for each PR. The first-N limits are not paginated: a head
# commit with more than 30 check suites (or 100 runs in one suite) could hide the named CI
# check, which would read as "no CI signal" and clear a correct CI emoji. Raise them before
# adding pagination if that ever bites; batched queries put ~3k nodes per PR against
# GitHub's 500k-node query ceiling, so there is room.
_PR_FIELDS = """
  number
  state
  title
  labels(first: 100) { nodes { name } }
  commits(last: 1) {
    nodes {
      commit {
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

    ``gh`` exits non-zero whenever the GraphQL payload carries *any* error, even though
    stdout still holds the response — including usable partial data (e.g. a batch query
    where one aliased PR number is an issue or was deleted yields NOT_FOUND for that alias
    and full data for the rest). So: parse stdout regardless of exit code, tolerate
    NOT_FOUND (the caller just sees a missing node), and raise only when there is no
    response at all or the errors are of some other kind.
    """
    proc = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict):
        stderr = proc.stderr.strip()
        raise RuntimeError(
            f"gh api graphql produced no response (exit {proc.returncode})"
            + (f": {stderr}" if stderr else "")
        )
    errors = [e for e in payload.get("errors") or [] if e.get("type") != "NOT_FOUND"]
    if errors:
        raise RuntimeError(f"GitHub GraphQL errors: {errors}")
    return payload.get("data") or {}


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
