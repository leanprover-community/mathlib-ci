"""What the report is about: repository, commit, workflow run, and display switches.

`context_from_env` reads it from the environment with the same names and fallbacks as
`zulip_build_report.sh`, so a caller can switch scripts without changing its step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass
class ReportContext:
    target_repo: str
    target_sha: str
    workflow_repo: str
    run_id: str
    workflow_name: str
    success: bool
    show_info: bool
    # Directory the paths in the log are relative to (the package directory `lake build`
    # ran in). Used to decide whether a path can be linked into `target_repo`.
    source_root: str = "."

    @property
    def run_url(self) -> str:
        return f"https://github.com/{self.workflow_repo}/actions/runs/{self.run_id}"

    @property
    def target_url(self) -> str:
        return f"https://github.com/{self.target_repo}"


def _first(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = env.get(name, "")
        if value:
            return value
    return ""


def context_from_env(env: Mapping[str, str]) -> ReportContext:
    """Read the report context from the environment, with the shell script's fallbacks.

    First non-empty wins:
      TARGET_REPO | REPO | GITHUB_REPOSITORY      repository the log was built from
      TARGET_SHA | SHA | GITHUB_SHA               commit that was built
      WORKFLOW_REPO | REPO | GITHUB_REPOSITORY    repository hosting the workflow run
      WORKFLOW_RUN_ID | RUN_ID | GITHUB_RUN_ID    run id, for the link to the run
      WORKFLOW | GITHUB_WORKFLOW                  workflow name, for the headline
      SUCCESS                                     "true" if the build step succeeded
      INFO                                        anything but "false" reports info messages
    """
    return ReportContext(
        target_repo=_first(env, "TARGET_REPO", "REPO", "GITHUB_REPOSITORY"),
        target_sha=_first(env, "TARGET_SHA", "SHA", "GITHUB_SHA"),
        workflow_repo=_first(env, "WORKFLOW_REPO", "REPO", "GITHUB_REPOSITORY"),
        run_id=_first(env, "WORKFLOW_RUN_ID", "RUN_ID", "GITHUB_RUN_ID"),
        workflow_name=_first(env, "WORKFLOW", "GITHUB_WORKFLOW"),
        success=env.get("SUCCESS", "") == "true",
        # `${INFO:-false}` in the shell script: unset or empty both mean false.
        show_info=(env.get("INFO") or "false") != "false",
    )
