"""Tests for `scripts/route/predict_cascade.py`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import predict_cascade as pc

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "route" / "predict_cascade.py"

# `B` imports `A`, `C` imports `B`; `D` imports nothing and nothing imports it.
DOT = """digraph "import_graph" {
  "Mathlib.D" [shape=ellipse];
  "Mathlib.A" -> "Mathlib.B";
  "Mathlib.B" -> "Mathlib.C";
}
"""


def count(changed):
    return pc.cascade(*pc.read_graph(DOT.splitlines()), changed)


def test_read_graph():
    dependents, modules = pc.read_graph(DOT.splitlines())
    assert modules == {"Mathlib.A", "Mathlib.B", "Mathlib.C", "Mathlib.D"}
    assert dependents == {"Mathlib.A": {"Mathlib.B"}, "Mathlib.B": {"Mathlib.C"}}


def test_module_name():
    assert pc.module_name("Mathlib/Algebra/Group/Defs.lean") == "Mathlib.Algebra.Group.Defs"
    assert pc.module_name("MathlibTest/foo.lean") is None
    assert pc.module_name("Mathlib.lean") is None
    assert pc.module_name("docs/Mathlib/x.md") is None


def test_cascade_counts_transitive_importers():
    assert count(["Mathlib/A.lean"]) == 3
    assert count(["Mathlib/B.lean"]) == 2
    assert count(["Mathlib/C.lean", "Mathlib/D.lean"]) == 2


def test_cascade_counts_each_module_once():
    assert count(["Mathlib/A.lean", "Mathlib/B.lean"]) == 3


def test_new_module_counts_once():
    assert count(["Mathlib/New.lean"]) == 1


def test_non_module_files_count_nothing():
    assert count(["docs/overview.yaml", "MathlibTest/A.lean", "Mathlib.lean", "scripts/x.py"]) == 0
    assert count([]) == 0


def test_rebuild_all_files_count_every_module():
    for path in ("lakefile.lean", "lake-manifest.json", "lean-toolchain"):
        assert count([path, "docs/x.md"]) == 4


def test_cli(tmp_path):
    (tmp_path / "graph.dot").write_text(DOT)
    (tmp_path / "changed.txt").write_text("Mathlib/B.lean\n\ndocs/x.md\n")
    out = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "graph.dot"), str(tmp_path / "changed.txt")],
        capture_output=True, check=True, text=True,
    )
    assert out.stdout.strip() == "2"
