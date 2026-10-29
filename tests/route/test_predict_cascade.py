"""Tests for `scripts/route/predict_cascade.py`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import predict_cascade as pc

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "route" / "predict_cascade.py"

# `Lattice` imports `Basic`, `Topology.Basic` imports `Lattice`; nothing imports `Nat.Defs`.
DOT = """digraph "import_graph" {
  "Mathlib.Data.Nat.Defs" [shape=ellipse];
  "Mathlib.Order.Basic" -> "Mathlib.Order.Lattice";
  "Mathlib.Order.Lattice" -> "Mathlib.Topology.Basic";
}
"""


def count(changed):
    return pc.cascade(*pc.read_graph(DOT.splitlines()), changed)


def test_counts_the_changed_module_and_its_transitive_importers():
    assert count(["Mathlib/Order/Basic.lean"]) == 3
    assert count(["Mathlib/Order/Lattice.lean"]) == 2
    assert count(["Mathlib/Topology/Basic.lean", "Mathlib/Data/Nat/Defs.lean"]) == 2


def test_counts_a_module_once_when_it_is_changed_and_an_importer():
    assert count(["Mathlib/Order/Basic.lean", "Mathlib/Order/Lattice.lean"]) == 3


def test_counts_a_module_the_graph_does_not_have_once():
    assert count(["Mathlib/Order/New.lean"]) == 1


def test_counts_no_module_for_files_outside_mathlib():
    assert count(["docs/overview.yaml", "MathlibTest/Order.lean", "Mathlib.lean", "scripts/x.py"]) == 0


def test_counts_every_module_for_a_build_configuration_file():
    for path in ("lakefile.lean", "lake-manifest.json", "lean-toolchain"):
        assert count([path]) == 4


def test_cli_reads_a_graph_file_and_a_file_list_with_blank_lines(tmp_path):
    (tmp_path / "graph.dot").write_text(DOT)
    (tmp_path / "changed.txt").write_text("Mathlib/Order/Lattice.lean\n\ndocs/x.md\n")
    out = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "graph.dot"), str(tmp_path / "changed.txt")],
        capture_output=True, check=True, text=True,
    )
    assert out.stdout.strip() == "2"
