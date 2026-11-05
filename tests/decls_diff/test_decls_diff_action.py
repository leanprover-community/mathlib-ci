"""Run the `decls-diff` composite action's shell step, as GitHub would, against dump
directories and against the bare files of callers that predate them."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_ACTION = _REPO / ".github" / "actions" / "decls-diff" / "action.yml"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run_script() -> str:
    """The `run: |` block of the step with `id: run`, dedented. Read by indentation
    rather than with a YAML library, which this suite does not install."""
    lines = _ACTION.read_text().splitlines()
    start = lines.index("    - id: run")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line)
    return textwrap.dedent("\n".join(body)) + "\n"


def _dump_dir(d: Path, names: list[str], with_api: bool = True) -> Path:
    """A dump directory with an `api.jsonl` or, from before the API dump, a `decls.txt`."""
    d.mkdir(parents=True)
    (d / "imports.json").write_text("{}")
    if with_api:
        rows = [{"schema": 1, "fields": {"n": 1, "k": 1, "m": 1, "mod": 1}}]
        rows += [{"n": n, "k": "thm", "m": "M"} for n in names]
        (d / "api.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    else:
        (d / "decls.txt").write_text("".join(n + "\n" for n in names))
    return d


def _action(tmp_path: Path, **inputs: str,
            ) -> tuple[subprocess.CompletedProcess[str], dict[str, str], str]:
    """Run the step with the given inputs (`reference_dir=...` for `reference-dir`).
    Return the process, the step outputs and the step summary."""
    out, summary = tmp_path / "github_output", tmp_path / "step_summary"
    out.touch()
    summary.touch()
    env = {k: v for k, v in os.environ.items() if not k.startswith("IN_")}
    env.update(CI_SCRIPTS_DIR=str(_REPO / "scripts"), RUNNER_TEMP=str(tmp_path),
               GITHUB_OUTPUT=str(out), GITHUB_STEP_SUMMARY=str(summary),
               IN_NEW_SHA="1234567890abcdef", IN_WITH_HEADING="true")
    names = {"reference_dir": "IN_REF_DIR", "new_dir": "IN_NEW_DIR",
             "reference_decls_file": "IN_REF_DECLS", "new_decls_file": "IN_NEW_DECLS",
             "reference_imports_file": "IN_REF_IMPORTS", "new_imports_file": "IN_NEW_IMPORTS"}
    for key, var in names.items():
        env[var] = inputs.get(key, "")
    proc = subprocess.run(["bash", "-c", _run_script()], env=env, capture_output=True, text=True)
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return proc, outputs, summary.read_text()


def test_dump_directories(tmp_path: Path) -> None:
    ref = _dump_dir(tmp_path / "ref", ["A", "B"])
    new = _dump_dir(tmp_path / "new", ["A", "C"])
    proc, outputs, summary = _action(tmp_path, reference_dir=str(ref), new_dir=str(new))
    assert proc.returncode == 0, proc.stderr
    assert outputs["plus-count"] == "1" and outputs["minus-count"] == "1"
    assert outputs["api-dump"] == "used"
    assert outputs["base-json"] == f"{ref}/imports.json"
    assert outputs["head-json"] == f"{new}/imports.json"
    assert Path(outputs["diff-file"]).read_text() == "-B\n+C\n"
    assert "* API dump: `used` api.jsonl on both sides; fields in common:" in summary


def test_bare_files_as_before(tmp_path: Path) -> None:
    """A caller still passing the four file paths gets the same diff, without API dumps."""
    ref = _dump_dir(tmp_path / "ref", ["A", "B"], with_api=False)
    new = _dump_dir(tmp_path / "new", ["A", "C"], with_api=False)
    proc, outputs, _ = _action(
        tmp_path,
        reference_decls_file=str(ref / "decls.txt"), new_decls_file=str(new / "decls.txt"),
        reference_imports_file=str(ref / "imports.json"),
        new_imports_file=str(new / "imports.json"))
    assert proc.returncode == 0, proc.stderr
    assert outputs["api-dump"] == "absent"
    assert outputs["base-json"] == str(ref / "imports.json")
    assert Path(outputs["diff-file"]).read_text() == "-B\n+C\n"


def test_reference_side_predates_the_api_dump(tmp_path: Path) -> None:
    ref = _dump_dir(tmp_path / "ref", ["A", "B"], with_api=False)
    new = _dump_dir(tmp_path / "new", ["A", "C"])
    proc, outputs, summary = _action(tmp_path, reference_dir=str(ref), new_dir=str(new))
    assert proc.returncode == 0, proc.stderr
    assert outputs["api-dump"] == "absent"
    assert Path(outputs["diff-file"]).read_text() == "-B\n+C\n"
    assert "* API dump: `absent` names only: the reference side has no api.jsonl" in summary


def test_unreadable_api_dump_fails_the_step(tmp_path: Path) -> None:
    ref = _dump_dir(tmp_path / "ref", ["A"])
    new = _dump_dir(tmp_path / "new", ["A"])
    (new / "api.jsonl").write_text("not json\n")
    proc, outputs, _ = _action(tmp_path, reference_dir=str(ref), new_dir=str(new))
    assert proc.returncode == 1
    assert "api.jsonl' is unusable: line 1 is not valid JSON" in proc.stderr
    assert "decls-override-file" not in outputs


@pytest.mark.parametrize("both", [True, False])
def test_exactly_one_input_per_side(tmp_path: Path, both: bool) -> None:
    ref = _dump_dir(tmp_path / "ref", ["A"])
    new = _dump_dir(tmp_path / "new", ["A"])
    extra = {"reference_decls_file": str(ref / "decls.txt")} if both else {}
    proc, _, _ = _action(tmp_path, **({"reference_dir": str(ref)} if both else {}),
                         new_dir=str(new), **extra)
    assert proc.returncode == 1
    assert "give exactly one of reference-dir and reference-decls-file" in proc.stderr
