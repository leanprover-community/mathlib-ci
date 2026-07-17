"""Tests for GitHub state parsing and the batched fetch layer (with a fake runner)."""

from __future__ import annotations

import copy

import pytest

from emoji_reconcile.config import parse_config
from emoji_reconcile.github_state import (
    ResourceLimitsExceeded,
    _classify_check_run,
    _classify_status_context,
    build_batch_query,
    ci_from_head_commit,
    fetch_pr_states,
    gh_graphql_runner,
    pr_state_from_node,
)
from emoji_reconcile.pr_state import CI_NONE

from conftest import SAMPLE_CONFIG_DATA


@pytest.fixture
def ci_named_config():
    """Config that only counts the 'continuous integration' workflow toward CI."""
    data = copy.deepcopy(SAMPLE_CONFIG_DATA)
    data["ci"] = {"check_names": ["continuous integration"]}
    return parse_config(data)


def check_run(name, status, conclusion, workflow=None):
    return {"name": name, "status": status, "conclusion": conclusion, "_workflow": workflow}


def head_commit(check_runs=None, statuses=None):
    """Build a GraphQL commit node. check_runs is a list of dicts from check_run()."""
    suites = []
    for run in check_runs or []:
        suites.append({
            "workflowRun": {"workflow": {"name": run.get("_workflow")}} if run.get("_workflow") else None,
            "checkRuns": {"nodes": [{
                "name": run["name"], "status": run["status"], "conclusion": run["conclusion"],
            }]},
        })
    return {
        "checkSuites": {"nodes": suites},
        "status": {"contexts": statuses or []},
    }


def pr_node(number=123, state="OPEN", labels=(), commit=None, title=None):
    return {
        "number": number,
        "state": state,
        "title": title,
        "labels": {"nodes": [{"name": n} for n in labels]},
        "commits": {"nodes": [{"commit": commit}]} if commit is not None else {"nodes": []},
    }


class TestClassifyCheckRun:
    def test_in_progress_is_running(self) -> None:
        assert _classify_check_run("IN_PROGRESS", None) == "running"
        assert _classify_check_run("QUEUED", None) == "running"

    def test_completed_success(self) -> None:
        assert _classify_check_run("COMPLETED", "SUCCESS") == "success"

    def test_completed_failures(self) -> None:
        for c in ["FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED"]:
            assert _classify_check_run("COMPLETED", c) == "failure"

    def test_completed_ignored_conclusions(self) -> None:
        for c in ["CANCELLED", "SKIPPED", "NEUTRAL", "STALE"]:
            assert _classify_check_run("COMPLETED", c) is None


class TestClassifyStatusContext:
    def test_pending_running(self) -> None:
        assert _classify_status_context("PENDING") == "running"
        assert _classify_status_context("EXPECTED") == "running"

    def test_success_and_failures(self) -> None:
        assert _classify_status_context("SUCCESS") == "success"
        assert _classify_status_context("ERROR") == "failure"
        assert _classify_status_context("FAILURE") == "failure"


class TestCiFromHeadCommit:
    def test_no_checks_is_none(self) -> None:
        assert ci_from_head_commit(head_commit(), ()) == CI_NONE

    def test_all_success(self) -> None:
        commit = head_commit([check_run("build", "COMPLETED", "SUCCESS")])
        assert ci_from_head_commit(commit, ()) == "success"

    def test_failure_outranks_running_and_success(self) -> None:
        # A failure surfaces immediately, even while another check is still in progress.
        commit = head_commit([
            check_run("a", "COMPLETED", "FAILURE"),
            check_run("b", "IN_PROGRESS", None),
            check_run("c", "COMPLETED", "SUCCESS"),
        ])
        assert ci_from_head_commit(commit, ()) == "failure"

    def test_running_outranks_success(self) -> None:
        # Green only once everything relevant has finished.
        commit = head_commit([
            check_run("b", "IN_PROGRESS", None),
            check_run("c", "COMPLETED", "SUCCESS"),
        ])
        assert ci_from_head_commit(commit, ()) == "running"

    def test_failure_outranks_success(self) -> None:
        commit = head_commit([
            check_run("a", "COMPLETED", "FAILURE"),
            check_run("c", "COMPLETED", "SUCCESS"),
        ])
        assert ci_from_head_commit(commit, ()) == "failure"

    def test_legacy_status_contexts_counted(self) -> None:
        commit = head_commit(statuses=[{"context": "bors", "state": "PENDING"}])
        assert ci_from_head_commit(commit, ()) == "running"

    def test_check_names_filter_by_workflow(self) -> None:
        # Only the named workflow's check should count; the unrelated failing one is ignored.
        commit = head_commit([
            check_run("build", "COMPLETED", "SUCCESS", workflow="continuous integration"),
            check_run("lint", "COMPLETED", "FAILURE", workflow="some other workflow"),
        ])
        assert ci_from_head_commit(commit, ("continuous integration",)) == "success"

    def test_check_names_filter_by_run_name(self) -> None:
        commit = head_commit([
            check_run("continuous integration", "IN_PROGRESS", None),
            check_run("lint", "COMPLETED", "FAILURE"),
        ])
        assert ci_from_head_commit(commit, ("continuous integration",)) == "running"

    def test_check_names_match_substrings_case_insensitively(self) -> None:
        # 'build' selects the reusable-workflow jobs 'ci / Build' and 'ci (fork) / Build'
        # without naming the caller-job prefix; the unrelated failing run stays invisible.
        commit = head_commit([
            check_run("ci / Build", "COMPLETED", "SUCCESS"),
            check_run("ci (fork) / Build", "COMPLETED", "SKIPPED"),
            check_run("set_pr_emoji", "COMPLETED", "FAILURE"),
        ])
        assert ci_from_head_commit(commit, ("build",)) == "success"


class TestPrStateFromNode:
    def test_open_with_labels(self, sample_config) -> None:
        node = pr_node(state="OPEN", labels=["awaiting-author", "maintainer-merge"])
        state = pr_state_from_node(node, sample_config)
        assert state.number == 123
        assert state.status == "open"
        assert state.labels == frozenset({"awaiting-author", "maintainer-merge"})
        assert state.ci == CI_NONE

    def test_merged_state(self, sample_config) -> None:
        assert pr_state_from_node(pr_node(state="MERGED"), sample_config).status == "merged"

    def test_closed_state(self, sample_config) -> None:
        assert pr_state_from_node(pr_node(state="CLOSED"), sample_config).status == "closed"

    def test_closed_with_bors_title_is_merged(self, sample_config) -> None:
        # bors closes (not merges) the PR on GitHub but renames the title; treat it as merged.
        node = pr_node(state="CLOSED", title="[Merged by Bors] - feat: a nice lemma")
        assert pr_state_from_node(node, sample_config).status == "merged"

    def test_closed_without_bors_title_stays_closed(self, sample_config) -> None:
        node = pr_node(state="CLOSED", title="feat: a nice lemma")
        assert pr_state_from_node(node, sample_config).status == "closed"

    def test_open_pr_with_bors_title_is_not_promoted(self, sample_config) -> None:
        # The prefix only promotes CLOSED PRs; the bot only renames on merge anyway.
        node = pr_node(state="OPEN", title="[Merged by Bors] - feat: a nice lemma")
        assert pr_state_from_node(node, sample_config).status == "open"

    def test_bors_title_ignored_when_prefix_unset(self) -> None:
        data = copy.deepcopy(SAMPLE_CONFIG_DATA)
        data.pop("merged_title_prefix", None)
        config = parse_config(data)
        node = pr_node(state="CLOSED", title="[Merged by Bors] - feat: a nice lemma")
        assert pr_state_from_node(node, config).status == "closed"

    def test_ci_derived(self, sample_config) -> None:
        commit = head_commit([check_run("build", "COMPLETED", "FAILURE")])
        state = pr_state_from_node(pr_node(commit=commit), sample_config)
        assert state.ci == "failure"

    def test_ci_respects_config_check_names(self, ci_named_config) -> None:
        commit = head_commit([
            check_run("build", "COMPLETED", "SUCCESS", workflow="continuous integration"),
            check_run("other", "IN_PROGRESS", None, workflow="unrelated"),
        ])
        state = pr_state_from_node(pr_node(commit=commit), ci_named_config)
        assert state.ci == "success"


class TestFetch:
    def test_fetch_single_pr(self, sample_config) -> None:
        node = pr_node(number=42, state="OPEN", labels=["delegated"])

        def runner(query):
            assert "pr42: pullRequest(number: 42)" in query
            return {"repository": {"pr42": node}}

        states = fetch_pr_states("leanprover-community/mathlib4", [42], sample_config, runner)
        assert states[42].number == 42 and states[42].labels == frozenset({"delegated"})

    def test_fetch_pr_states_batches_and_chunks(self, sample_config) -> None:
        calls = []

        def runner(query):
            calls.append(query)
            # Return a node for each alias present in the query.
            repo = {}
            for n in (1, 2, 3):
                if f"pr{n}:" in query:
                    repo[f"pr{n}"] = pr_node(number=n, labels=["delegated"])
            return {"repository": repo}

        states = fetch_pr_states(
            "leanprover-community/mathlib4", [3, 1, 2, 2], sample_config,
            runner=runner, chunk_size=2,
        )
        assert set(states) == {1, 2, 3}
        # 3 unique PRs, chunk_size 2 -> 2 queries.
        assert len(calls) == 2

    def test_fetch_pr_states_omits_missing(self, sample_config) -> None:
        def runner(query):
            return {"repository": {"pr1": pr_node(number=1)}}  # pr2 absent

        states = fetch_pr_states(
            "leanprover-community/mathlib4", [1, 2], sample_config, runner=runner
        )
        assert set(states) == {1}

    def test_fetch_pr_states_bisects_on_resource_limits(self, sample_config) -> None:
        # GitHub rejects the query whenever it asks for 3+ PRs at once; the fetch must
        # split the batch until each piece fits, and still return every PR.
        calls = []

        def runner(query):
            aliased = [n for n in range(1, 6) if f"pr{n}:" in query]
            calls.append(aliased)
            if len(aliased) >= 3:
                raise ResourceLimitsExceeded("too expensive")
            return {"repository": {f"pr{n}": pr_node(number=n) for n in aliased}}

        states = fetch_pr_states(
            "leanprover-community/mathlib4", [1, 2, 3, 4, 5], sample_config,
            runner=runner, chunk_size=5,
        )
        assert set(states) == {1, 2, 3, 4, 5}
        # [1..5] rejected -> [1,2] ok, [3,4,5] rejected -> [3] ok, [4,5] ok.
        assert calls == [[1, 2, 3, 4, 5], [1, 2], [3, 4, 5], [3], [4, 5]]

    def test_fetch_pr_states_skips_single_pr_over_limits(self, sample_config) -> None:
        # A PR too expensive to fetch even alone is skipped with a warning; the rest of
        # the batch still comes back.
        logged = []

        def runner(query):
            aliased = [n for n in (1, 2) if f"pr{n}:" in query]
            if 2 in aliased:
                raise ResourceLimitsExceeded("too expensive")
            return {"repository": {f"pr{n}": pr_node(number=n) for n in aliased}}

        states = fetch_pr_states(
            "leanprover-community/mathlib4", [1, 2], sample_config,
            runner=runner, chunk_size=2, log=logged.append,
        )
        assert set(states) == {1}
        assert len(logged) == 1 and "PR #2" in logged[0]


class TestQueryBuilders:
    def test_batch_query_has_aliases(self) -> None:
        q = build_batch_query("owner/repo", [1, 2])
        assert 'repository(owner: "owner", name: "repo")' in q
        assert "pr1: pullRequest(number: 1)" in q
        assert "pr2: pullRequest(number: 2)" in q

    def test_bad_repo_raises(self) -> None:
        with pytest.raises(ValueError, match="owner/name"):
            build_batch_query("not-a-slug", [1])


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class TestGhGraphqlRunner:
    """gh exits non-zero whenever the payload has errors; partial data must survive."""

    def _patch(self, monkeypatch, proc):
        import emoji_reconcile.github_state as gs
        monkeypatch.setattr(gs.subprocess, "run", lambda *a, **k: proc)

    def test_success(self, monkeypatch) -> None:
        self._patch(monkeypatch, FakeProc(stdout='{"data": {"repository": {}}}'))
        assert gh_graphql_runner("query {}") == {"repository": {}}

    def test_not_found_errors_tolerated_with_partial_data(self, monkeypatch) -> None:
        # One missing PR in a batch: gh exits 1 but stdout has the other PRs' data.
        payload = ('{"data": {"repository": {"pr1": {"number": 1}, "pr2": null}}, '
                   '"errors": [{"type": "NOT_FOUND", "message": "no PR 2"}]}')
        self._patch(monkeypatch, FakeProc(stdout=payload, returncode=1))
        data = gh_graphql_runner("query {}")
        assert data["repository"]["pr1"] == {"number": 1}
        assert data["repository"]["pr2"] is None

    def test_other_errors_raise(self, monkeypatch) -> None:
        payload = '{"data": null, "errors": [{"type": "RATE_LIMITED", "message": "slow down"}]}'
        self._patch(monkeypatch, FakeProc(stdout=payload, returncode=1))
        with pytest.raises(RuntimeError, match="RATE_LIMITED"):
            gh_graphql_runner("query {}")

    def test_resource_limits_raise_typed_error(self, monkeypatch) -> None:
        # All non-NOT_FOUND errors are RESOURCE_LIMITS_EXCEEDED -> the typed exception,
        # so fetch_pr_states can bisect. Partial data must NOT be returned: the errored
        # paths are nulled, which could misread CI state.
        payload = ('{"data": {"repository": {"pr1": {"number": 1}}}, "errors": ['
                   '{"type": "NOT_FOUND", "message": "no PR 2"}, '
                   '{"type": "RESOURCE_LIMITS_EXCEEDED", "message": "Resource limits for this query exceeded."}]}')
        self._patch(monkeypatch, FakeProc(stdout=payload, returncode=1))
        with pytest.raises(ResourceLimitsExceeded):
            gh_graphql_runner("query {}")

    def test_mixed_error_types_raise_generic(self, monkeypatch) -> None:
        # A response mixing RESOURCE_LIMITS_EXCEEDED with an unrelated error is not safe
        # to retry-by-bisection; it must surface as the generic failure.
        payload = ('{"data": null, "errors": ['
                   '{"type": "RESOURCE_LIMITS_EXCEEDED", "message": "too big"}, '
                   '{"type": "RATE_LIMITED", "message": "slow down"}]}')
        self._patch(monkeypatch, FakeProc(stdout=payload, returncode=1))
        with pytest.raises(RuntimeError, match="RATE_LIMITED"):
            gh_graphql_runner("query {}")

    def test_no_output_retries_then_raises(self, monkeypatch) -> None:
        import emoji_reconcile.github_state as gs
        sleeps, procs = [], []
        monkeypatch.setattr(gs.time, "sleep", sleeps.append)
        monkeypatch.setattr(
            gs.subprocess, "run",
            lambda *a, **k: procs.append(None) or FakeProc(
                stdout="", stderr="gh: HTTP 502", returncode=1))
        with pytest.raises(RuntimeError, match=r"HTTP 502"):
            gh_graphql_runner("query {}")
        assert len(procs) == 4  # initial attempt + one per backoff step
        assert sleeps == list(gs._TRANSIENT_BACKOFF_SECONDS)

    def test_no_output_recovers_on_retry(self, monkeypatch) -> None:
        # First attempt is a bare gateway error, second returns data: the caller never
        # sees the blip.
        import emoji_reconcile.github_state as gs
        monkeypatch.setattr(gs.time, "sleep", lambda s: None)
        procs = iter([
            FakeProc(stdout="", stderr="gh: HTTP 502", returncode=1),
            FakeProc(stdout='{"data": {"repository": {}}}'),
        ])
        monkeypatch.setattr(gs.subprocess, "run", lambda *a, **k: next(procs))
        assert gh_graphql_runner("query {}") == {"repository": {}}
