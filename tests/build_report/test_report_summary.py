"""Tests for the job-summary rendering in build_report/summary.py."""

from __future__ import annotations

from build_report.context import ReportContext
from build_report.lake_log import UNATTRIBUTED, Message, classify, parse_build_log
from build_report.summary import render_summary, source_link

CTX = ReportContext(
    target_repo="leanprover/cslib",
    target_sha="492d030f13f42202bb3e01d67816095487d90493",
    workflow_repo="leanprover/cslib",
    run_id="7",
    workflow_name="Weekly linting report",
    success=False,
    show_info=True,
)
BLOB = "https://github.com/leanprover/cslib/blob/492d030f13f42202bb3e01d67816095487d90493"
VERSO = "\n\nNote: This linter can be disabled with `set_option linter.style.docStringVerso false`"


def _msgs(text):
    return classify(parse_build_log(text.splitlines()))


def _ctx(**overrides):
    return ReportContext(**{**CTX.__dict__, **overrides})


def test_source_link_forms(tmp_path):
    (tmp_path / "Cslib").mkdir()
    (tmp_path / "Cslib" / "A.lean").write_text("", encoding="utf-8")
    ctx = _ctx(source_root=str(tmp_path))
    assert source_link(Message("warning", "Cslib/A.lean", 12, 3, "x"), ctx) == f"[Cslib/A.lean:12]({BLOB}/Cslib/A.lean#L12)"
    assert source_link(Message("warning", "Cslib/Missing.lean", 1, 0, "x"), ctx) == "Cslib/Missing.lean:1"
    assert source_link(Message("error", None, None, None, "build failed"), ctx) == "(no position)"


def test_sections_order_and_headings():
    msgs = _msgs(
        "info: B.lean:1:0: 'simp; grind' can be replaced with 'grind'\n"
        "warning: A.lean:2:0: w1" + VERSO + "\n"
        "warning: A.lean:1:0: w0" + VERSO + "\n"
        "error: A.lean:9:0: boom\n"
        "info: A.lean:3:0: PANIC at foo\n"
        "warning: C.lean:1:0: untagged\n"
    )
    out = render_summary(msgs, CTX)
    headings = [l for l in out.splitlines() if l.startswith("## ")]
    assert headings == [
        "## Errors (1)",
        "## Panics (1)",
        "## linter.style.docStringVerso (2 warnings)",
        f"## {UNATTRIBUTED} (1 warnings, 1 info)",
    ]
    verso = out.split("## linter.style.docStringVerso (2 warnings)")[1].split("## ")[0]
    assert verso.index("A.lean:1 ") < verso.index("A.lean:2 ")
    assert "| A.lean:1 | w0 |" in verso


def test_unpositioned_rows_sort_last():
    out = render_summary(_msgs("error: build failed\nerror: Z.lean:1:0: z\nerror: A.lean:1:0: a\n"), CTX)
    assert out.index("| a |") < out.index("| z |") < out.index("| (no position) | build failed |")


def test_header_and_counts():
    out = render_summary(_msgs("warning: A.lean:1:0: w" + VERSO + "\n"), CTX)
    assert out.startswith("# Weekly linting report: leanprover/cslib @ 492d030\n")
    assert "Run [7](https://github.com/leanprover/cslib/actions/runs/7)." in out
    assert "* Warnings: 1\n" in out


def test_pipe_in_message_is_escaped():
    out = render_summary(_msgs("warning: A.lean:1:0: a | b" + VERSO + "\n"), CTX)
    assert "| a \\| b |" in out


def test_info_hidden():
    out = render_summary(_msgs("info: A.lean:1:0: i\nwarning: A.lean:2:0: w\n"), _ctx(show_info=False))
    assert "| i |" not in out
    assert "Info messages" not in out
    assert f"## {UNATTRIBUTED} (1 warnings)" in out


def test_size_cap_truncates_largest_section():
    log = "".join(f"warning: A.lean:{i}:0: {'x' * 50}{VERSO}\n" for i in range(200))
    log += "warning: B.lean:1:0: lonely\n"
    out = render_summary(_msgs(log), CTX, limit=6000)
    assert len(out.encode("utf-8")) <= 6000
    assert "more rows not shown |" in out
    assert "| lonely |" in out


def test_mathlib_fixture_renders_within_limit(mathlib_log):
    with open(mathlib_log, encoding="utf-8") as f:
        msgs = _msgs(f.read())
    out = render_summary(msgs, CTX)
    assert "## linter.style.docStringVerso (3 warnings)" in out
    assert "more rows not shown" not in out
    # One table row per message; the two header lines per section are not rows.
    assert out.count("\n| ") - out.count("\n| --- |") - out.count("\n| Location |") == 6 + 2


def test_links_only_for_files_present_in_checkout(tmp_path):
    (tmp_path / "Cslib").mkdir()
    (tmp_path / "Cslib" / "A.lean").write_text("", encoding="utf-8")
    ctx = _ctx(source_root=str(tmp_path))
    # Lake logs dependency paths relative to the dependency's own package dir, so a
    # Batteries warning looks like `Batteries/...`, not `.lake/packages/...`.
    assert source_link(Message("warning", "Cslib/A.lean", 12, 3, "x"), ctx) == f"[Cslib/A.lean:12]({BLOB}/Cslib/A.lean#L12)"
    assert source_link(Message("warning", "Batteries/Data/List/Basic.lean", 1, 0, "x"), ctx) == "Batteries/Data/List/Basic.lean:1"


def test_panics_section_has_one_row_per_panic_line(failed_log):
    with open(failed_log, encoding="utf-8") as f:
        msgs = _msgs(f.read())
    out = render_summary(msgs, CTX)
    panics = out.split("## Panics (2)")[1].split("## ")[0]
    assert panics.count("\n| (no position) | PANIC at ") == 2
    assert "stderr:" not in panics
