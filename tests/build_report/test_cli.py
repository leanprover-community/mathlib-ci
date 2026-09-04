"""End-to-end tests: run zulip_build_report.py as CI does."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from zulip_build_report import context_from_env

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "reporting" / "zulip_build_report.py"
_OURS = ("TARGET_", "WORKFLOW", "GITHUB_", "SHA", "REPO", "RUN_ID", "INFO", "SUCCESS")


def _run(logfile, env_extra, tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(_OURS)}
    env.update(env_extra)
    summary = tmp_path / "summary.md"
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    proc = subprocess.run([sys.executable, str(SCRIPT), str(logfile)], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    return proc, summary


def test_context_precedence():
    ctx = context_from_env({
        "REPO": "a/b", "SHA": "s1", "RUN_ID": "1", "GITHUB_WORKFLOW": "W",
        "TARGET_REPO": "c/d", "TARGET_SHA": "s2", "WORKFLOW_REPO": "e/f", "WORKFLOW_RUN_ID": "2", "WORKFLOW": "X",
        "SUCCESS": "true", "INFO": "true",
    })
    assert (ctx.target_repo, ctx.target_sha, ctx.workflow_repo, ctx.run_id, ctx.workflow_name) == ("c/d", "s2", "e/f", "2", "X")
    assert ctx.success and ctx.show_info


def test_context_defaults():
    ctx = context_from_env({"GITHUB_REPOSITORY": "a/b", "GITHUB_SHA": "s", "GITHUB_RUN_ID": "9", "GITHUB_WORKFLOW": "W"})
    assert (ctx.target_repo, ctx.workflow_repo, ctx.run_id, ctx.workflow_name) == ("a/b", "a/b", "9", "W")
    assert not ctx.success and not ctx.show_info
    assert context_from_env({"INFO": "yes"}).show_info
    assert not context_from_env({"INFO": "false"}).show_info


def test_mathlib_end_to_end(mathlib_log, tmp_path):
    proc, summary = _run(mathlib_log, {
        "SHA": "1055fdaf3aa7a12029847ebacbb74e8f6886e57d",
        "REPO": "leanprover-community/mathlib4",
        "RUN_ID": "33358987484",
        "INFO": "true",
        "GITHUB_WORKFLOW": "Weekly linting report",
    }, tmp_path)
    m = re.match(r"zulip-message<<(\S+)\n(.*)\n\1\n$", proc.stdout, re.S)
    assert m, proc.stdout
    zulip = m.group(2)
    assert zulip.startswith("❌ Weekly linting report run on [leanprover-community/mathlib4]")
    assert "* Warnings: 792\n* Info messages: 36" in zulip
    assert "| | linter.style.docStringVerso | 713 | 0 |" in zulip
    assert "https://github.com/leanprover-community/mathlib4/actions/runs/33358987484" in zulip
    assert len(zulip) < 10000
    assert "2658 lines of output" in proc.stderr
    assert "792 lines of warnings" in proc.stderr
    assert "36 lines of info" in proc.stderr
    text = summary.read_text(encoding="utf-8")
    assert "## linter.style.docStringVerso (713 warnings)" in text
    assert (
        "https://github.com/leanprover-community/mathlib4/blob/1055fdaf3aa7a12029847ebacbb74e8f6886e57d"
        "/Mathlib/Tactic/CrossRefAttribute.lean#L217"
    ) in text


def test_cslib_end_to_end(cslib_log, tmp_path):
    proc, summary = _run(cslib_log, {
        "REPO": "leanprover/cslib", "SHA": "492d030", "RUN_ID": "1", "INFO": "true",
        "GITHUB_WORKFLOW": "Weekly linting report",
    }, tmp_path)
    assert "* Info messages: 3" in proc.stdout
    assert "| | (not attributed to a linter) | 0 | 3 |" in proc.stdout
    assert summary.exists()


def test_summary_is_appended_not_overwritten(cslib_log, tmp_path):
    (tmp_path / "summary.md").write_text("previous\n", encoding="utf-8")
    _, summary = _run(cslib_log, {"REPO": "a/b", "SHA": "s", "RUN_ID": "1", "GITHUB_WORKFLOW": "W"}, tmp_path)
    assert summary.read_text(encoding="utf-8").startswith("previous\n# W: a/b @ s\n")


def test_no_summary_env_is_fine(cslib_log, tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(_OURS)}
    env.update({"REPO": "a/b", "SHA": "s", "RUN_ID": "1", "GITHUB_WORKFLOW": "W"})
    proc = subprocess.run([sys.executable, str(SCRIPT), str(cslib_log)], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("zulip-message<<")


def test_usage_error():
    proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 2
    assert "usage:" in proc.stderr
