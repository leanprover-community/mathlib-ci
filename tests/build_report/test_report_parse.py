"""Tests for parse_build_log / classify in zulip_build_report.py."""

from __future__ import annotations

from zulip_build_report import UNATTRIBUTED, Message, classify, parse_build_log


def _parse(text):
    return parse_build_log(text.splitlines())


def test_positioned_message_fields():
    msgs = _parse("warning: Mathlib/A.lean:12:3: something odd\n")
    assert msgs == [Message("warning", "Mathlib/A.lean", 12, 3, "something odd", None)]


def test_unpositioned_error():
    msgs = _parse("error: build failed\n")
    assert msgs == [Message("error", None, None, None, "build failed", None)]


def test_progress_lines_are_skipped_and_terminate_messages():
    log = (
        "⚠ [1/2] Built Mathlib.A (1s)\n"
        "warning: Mathlib/A.lean:1:0: first\n"
        "✔ [2/2] Built Mathlib.B (1s)\n"
        "ℹ [3/3] Built Mathlib.C (1s)\n"
        "info: Mathlib/C.lean:2:0: second\n"
        "Build completed successfully (3 jobs).\n"
    )
    msgs = _parse(log)
    assert [m.text for m in msgs] == ["first", "second"]


def test_continuation_lines_join_and_trailing_blanks_drop():
    log = (
        "warning: Mathlib/A.lean:5:6: `grind?` suggestion failed: `grind only [= a,\n"
        "  = b]` did not close the goal\n"
        "\n"
        "Note: This linter can be disabled with `set_option linter.tacticAnalysis.verifyGrindOnly false`\n"
        "✔ [1/1] Built Mathlib.A (1s)\n"
    )
    (m,) = _parse(log)
    assert m.text.splitlines()[0].endswith("`grind only [= a,")
    assert m.text.splitlines()[1] == "  = b]` did not close the goal"
    assert m.text.endswith("verifyGrindOnly false`")
    assert m.first_line == "`grind?` suggestion failed: `grind only [= a,"


def test_message_with_literal_newline_in_quotes():
    log = (
        "warning: Mathlib/A.lean:548:0: unexpected '\n"
        "'; expected positional argument\n"
        "\n"
        "Note: This linter can be disabled with `set_option linter.style.docStringVerso false`\n"
    )
    (m,) = _parse(log)
    assert m.text.startswith("unexpected '\n'; expected positional argument")


def test_lines_before_first_message_are_ignored():
    msgs = _parse("shell: /usr/bin/bash\nenv:\n  X: 1\n\nwarning: A.lean:1:0: hi\n")
    assert [m.text for m in msgs] == ["hi"]


def test_panic_detection():
    (m,) = _parse("info: Mathlib/A.lean:1:0: PANIC at Lean.Expr.foo Lean/Expr.lean:12:3: boom\n")
    assert m.is_panic
    (n,) = _parse("info: Mathlib/A.lean:1:0: 'simp; grind' can be replaced with 'grind'\n")
    assert not n.is_panic


def test_classify_uses_disable_note():
    log = (
        "warning: A.lean:1:0: x\n\nNote: This linter can be disabled with `set_option linter.style.docStringVerso false`\n"
        "info: A.lean:2:0: y\n"
        "error: A.lean:3:0: z\n\nNote: This linter can be disabled with `set_option linter.foo false`\n"
    )
    msgs = classify(_parse(log))
    assert [m.linter for m in msgs] == ["linter.style.docStringVerso", UNATTRIBUTED, "linter.foo"]


def test_mathlib_fixture_counts(mathlib_log):
    with open(mathlib_log, encoding="utf-8") as f:
        msgs = classify(parse_build_log(f))
    by_sev = {}
    for m in msgs:
        by_sev[m.severity] = by_sev.get(m.severity, 0) + 1
    assert by_sev == {"warning": 792, "info": 36}
    verso = [m for m in msgs if m.linter == "linter.style.docStringVerso"]
    assert len(verso) == 713
    assert all(m.severity == "warning" for m in verso)
    # verifyGrindOnly messages are untagged in this log (pre mathlib4#43399)
    grind = [m for m in msgs if m.first_line.startswith("`grind?` suggestion failed")]
    assert len(grind) == 792 - 713
    assert all(m.linter == UNATTRIBUTED for m in grind)
    assert all(m.linter == UNATTRIBUTED for m in msgs if m.severity == "info")


def test_cslib_fixture_counts(cslib_log):
    with open(cslib_log, encoding="utf-8") as f:
        msgs = classify(parse_build_log(f))
    assert [m.severity for m in msgs] == ["info"] * 3
    assert msgs[0].file == "Cslib/Foundations/Semantics/LTS/MapHom.lean"
    assert msgs[0].line == 51
