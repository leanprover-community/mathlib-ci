"""Tests for the Zulip rendering in zulip_build_report.py."""

from __future__ import annotations

from zulip_build_report import (
    UNATTRIBUTED,
    ReportContext,
    classify,
    linter_table,
    parse_build_log,
    render_zulip,
    severity_counts,
)

CTX = ReportContext(
    target_repo="leanprover-community/mathlib4",
    target_sha="abc123",
    workflow_repo="leanprover-community/mathlib4",
    run_id="42",
    workflow_name="Weekly linting report",
    success=False,
    show_info=True,
)
RUN_URL = "https://github.com/leanprover-community/mathlib4/actions/runs/42"
VERSO = "\n\nNote: This linter can be disabled with `set_option linter.style.docStringVerso false`"


def _msgs(text):
    return classify(parse_build_log(text.splitlines()))


def _ctx(**overrides):
    return ReportContext(**{**CTX.__dict__, **overrides})


def test_headline_without_messages():
    out = render_zulip([], CTX)
    assert out == (
        "❌ Weekly linting report run on [leanprover-community/mathlib4](https://github.com/leanprover-community/mathlib4) "
        "(commit [abc123](https://github.com/leanprover-community/mathlib4/commit/abc123)) "
        f"[failed without messages]({RUN_URL}).\n"
    )


def test_success_icon_and_wording():
    out = render_zulip([], _ctx(success=True))
    assert out.startswith("✅ ")
    assert "[succeeded without messages]" in out


def test_severity_counts_order_and_panics():
    msgs = _msgs(
        "info: A.lean:1:0: PANIC at foo\n"
        "error: A.lean:2:0: e\n"
        "warning: A.lean:3:0: w\n"
        "info: A.lean:4:0: i\n"
    )
    assert list(severity_counts(msgs).items()) == [
        ("Panics", 1), ("Errors", 1), ("Warnings", 1), ("Info messages", 1)
    ]


def test_linter_table_sorting_and_unattributed_last():
    msgs = _msgs(
        "warning: A.lean:1:0: a" + VERSO + "\n"
        "warning: A.lean:2:0: b" + VERSO + "\n"
        "info: A.lean:3:0: c\n\nNote: This linter can be disabled with `set_option linter.tacticAnalysis.mergeWithGrind false`\n"
        "warning: A.lean:4:0: d\n"
        "error: A.lean:5:0: e\n"
    )
    assert linter_table(msgs, show_info=True) == [
        ("linter.style.docStringVerso", 2, 0),
        ("linter.tacticAnalysis.mergeWithGrind", 0, 1),
        (UNATTRIBUTED, 1, 0),
    ]
    assert linter_table(msgs, show_info=False) == [
        ("linter.style.docStringVerso", 2, 0),
        (UNATTRIBUTED, 1, 0),
    ]


def test_full_message_layout():
    msgs = _msgs(
        "warning: A.lean:1:0: a" + VERSO + "\n"
        "info: A.lean:3:0: c\n"
        "error: A.lean:5:0: overloaded, errors\n"
        "error: build failed\n"
    )
    out = render_zulip(msgs, CTX)
    expected = (
        "❌ Weekly linting report run on [leanprover-community/mathlib4](https://github.com/leanprover-community/mathlib4) "
        "(commit [abc123](https://github.com/leanprover-community/mathlib4/commit/abc123)) "
        f"[failed with messages]({RUN_URL}):\n"
        "\n* Errors: 2\n* Warnings: 1\n* Info messages: 1\n\n"
        "| | Linter | Warnings | Info |\n"
        "| ---: | --- | ---: | ---: |\n"
        "| | linter.style.docStringVerso | 1 | 0 |\n"
        f"| | {UNATTRIBUTED} | 0 | 1 |\n"
        "\n"
        f"Full per-linter tables, with source links, are in the [job summary]({RUN_URL}).\n"
        "\n"
        "```spoiler Error counts\n"
        "| | Error description |\n"
        "| ---: | --- |\n"
        "| 1 | overloaded, errors |\n"
        "| 1 | build failed |\n"
        "```\n"
        "\n"
    )
    assert out == expected


def test_info_hidden_drops_column_but_keeps_bullet():
    msgs = _msgs("warning: A.lean:1:0: a" + VERSO + "\ninfo: A.lean:3:0: c\n")
    out = render_zulip(msgs, _ctx(show_info=False))
    assert "* Info messages: 1" in out
    assert "| | Linter | Warnings |\n| ---: | --- | ---: |\n" in out
    assert "| Info |" not in out


def test_only_hidden_info_means_no_linter_table():
    out = render_zulip(_msgs("info: A.lean:3:0: c\n"), _ctx(show_info=False))
    assert "* Info messages: 1" in out
    assert "| Linter |" not in out
    assert "job summary" not in out


def test_warnings_never_listed_individually(mathlib_log):
    with open(mathlib_log, encoding="utf-8") as f:
        msgs = _msgs(f.read())
    out = render_zulip(msgs, CTX)
    assert "spoiler Warning counts" not in out
    assert "spoiler Info message counts" not in out
    assert "| | linter.style.docStringVerso | 713 | 0 |" in out
    assert f"| | {UNATTRIBUTED} | 79 | 36 |" in out
    assert len(out) < 2000


def test_panic_table_precedes_error_table():
    msgs = _msgs("info: A.lean:1:0: PANIC at foo\nerror: A.lean:2:0: bad\n")
    out = render_zulip(msgs, CTX)
    assert out.index("spoiler Panic counts") < out.index("spoiler Error counts")
    assert "| 1 | PANIC at foo |" in out


def test_error_rows_sorted_like_sort_uniq_c_sort_bgr():
    # The shell script used `sort | uniq -c | sort -bgr`: count descending, and `-r`
    # also reverses the tie-break on the text, so ties come in reverse byte order.
    msgs = _msgs("error: A.lean:1:0: aa\nerror: A.lean:2:0: mm\nerror: A.lean:3:0: zz\nerror: A.lean:4:0: mm\n")
    out = render_zulip(msgs, CTX)
    assert "| 2 | mm |\n| 1 | zz |\n| 1 | aa |\n" in out
