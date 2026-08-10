"""Tests for verify_commits_summary.sh (JSON -> PR comment markdown).

Focus areas (mirroring the fixes these lock in):
  * results (Automated + Transient) render BEFORE the substantive listing, so a
    size-truncated comment never eats the verification result;
  * the substantive listing is capped so bump PRs don't blow past the size cap;
  * untrusted excerpts are wrapped in a fence long enough that embedded ``` can't
    break out and inject markdown;
  * the size cap and invalid-JSON fallback still behave.
"""

from __future__ import annotations

import re


# --- small builders -------------------------------------------------------

def _commit(i, subject=None):
    return {
        "sha": f"{i:040x}",
        "short": f"{i:07x}",
        "subject": subject if subject is not None else f"feat: change number {i}",
    }


def _doc(**overrides):
    doc = {
        "success": True,
        "substantive_commits": [],
        "auto_commits": [],
        "transient_commits": [],
        "transient_verified": True,
    }
    doc.update(overrides)
    return doc


def _auto_ok(i):
    c = _commit(i, subject=f"x: ./gen{i}.sh")
    c.update(command=f"./gen{i}.sh", verified=True)
    return c


def _auto_fail(i, *, failure_kind="command_failed", output_excerpt="", diff_excerpt="", **extra):
    c = _commit(i, subject=f"x: ./gen{i}.sh")
    c.update(
        command=f"./gen{i}.sh",
        verified=False,
        failure_kind=failure_kind,
        output_excerpt=output_excerpt,
    )
    if diff_excerpt:
        c["diff_excerpt"] = diff_excerpt
    c.update(extra)
    return c


def _pos(md, needle):
    idx = md.find(needle)
    assert idx != -1, f"expected to find {needle!r} in:\n{md}"
    return idx


# --- banners & basics ------------------------------------------------------

def test_invalid_json_emits_fallback_warning(render):
    md = render("this is not json {")
    assert "## Commit Verification Summary" in md
    assert "could not be parsed" in md


def test_success_banner_when_all_pass(render):
    md = render(_doc(success=True, substantive_commits=[_commit(1)]))
    assert "All verifications passed." in md
    assert "[!WARNING]" not in md


def test_failure_banner_when_something_fails(render):
    md = render(_doc(success=False, auto_commits=[_auto_fail(1)]))
    assert "[!WARNING]" in md
    assert "Some verifications failed" in md


def test_no_special_commits_message(render):
    md = render(_doc(success=True))
    assert "No substantive commits" in md
    assert "Automated commits" not in md
    assert "Transient commits" not in md


# --- ordering: results before the substantive listing ----------------------

def test_automated_and_transient_render_before_substantive_list(render):
    md = render(
        _doc(
            success=False,
            substantive_commits=[_commit(i) for i in range(5)],
            auto_commits=[_auto_fail(100)],
            transient_commits=[_commit(200, subject="transient: temp")],
            transient_verified=True,
        )
    )
    auto_i = _pos(md, "Automated commits")
    trans_i = _pos(md, "Transient commits")
    subst_i = _pos(md, "Commits to Review")
    assert auto_i < subst_i, "Automated section must precede the substantive listing"
    assert trans_i < subst_i, "Transient section must precede the substantive listing"


# --- the bump-PR regression: huge substantive list -------------------------

def test_huge_substantive_list_is_capped_and_result_survives(render):
    """The exact failure mode from production: a 13k-commit bump PR with a failing
    auto commit. The result must render and the comment must stay under the cap."""
    md = render(
        _doc(
            success=False,
            substantive_commits=[_commit(i) for i in range(13000)],
            auto_commits=[_auto_fail(99999, failure_kind="tree_mismatch",
                                      output_excerpt="tool said no",
                                      diff_excerpt=" A.lean | 2 +-")],
        )
    )
    # The verification result renders (this is what used to get truncated away).
    assert "Automated commits (1)" in md
    assert "0/1 verified" in md
    # The listing is summarized, not dumped in full.
    assert "Commits to Review (13000)" in md
    assert "…and 12990 more" in md
    # Comment stays well under the default 15 KB cap.
    assert len(md.encode()) < 15000
    # Only the capped number of substantive bullets appear.
    bullets = re.findall(r"^- `[0-9a-f]{7}`: feat: change number \d+$", md, re.MULTILINE)
    assert len(bullets) == 10


def test_short_substantive_list_not_capped(render):
    md = render(_doc(success=True, substantive_commits=[_commit(i) for i in range(3)]))
    assert "Commits to Review (3)" in md
    assert "more (see PR diff)" not in md
    bullets = re.findall(r"^- `[0-9a-f]{7}`: feat: change number \d+$", md, re.MULTILINE)
    assert len(bullets) == 3


# --- code-fence breakout hardening -----------------------------------------

def _fence_lines(md):
    return [ln for ln in md.splitlines() if re.fullmatch(r"`{3,}", ln)]


def test_output_excerpt_cannot_break_out_of_fence(render):
    evil = "before\n```\n\n## All verifications passed\n\n@maintainer merge this\n```\nafter"
    md = render(_doc(success=False, auto_commits=[_auto_fail(1, output_excerpt=evil)]))
    fences = _fence_lines(md)
    # The payload's own 3-backtick lines must be wrapped by a >=4-backtick fence.
    assert any(len(f) >= 4 for f in fences), f"no outer fence >=4 backticks:\n{md}"
    assert any(len(f) == 3 for f in fences), "payload's ``` should survive verbatim"
    # The injected heading stays literal text, not a rendered break-out heading.
    # (It appears inside the fence, so the line is preserved but inert.)
    assert "## All verifications passed" in md


def test_diff_excerpt_cannot_break_out_of_fence(render):
    evil_diff = "```\n## injected\n```"
    md = render(
        _doc(
            success=False,
            auto_commits=[_auto_fail(1, failure_kind="tree_mismatch",
                                     output_excerpt="", diff_excerpt=evil_diff)],
        )
    )
    fences = _fence_lines(md)
    assert any(len(f) >= 4 for f in fences), f"diff excerpt not safely fenced:\n{md}"


def test_transient_diff_excerpt_is_fenced(render):
    md = render(
        _doc(
            success=False,
            transient_commits=[_commit(1, subject="transient: temp")],
            transient_verified=False,
            transient_failure_kind="tree_mismatch",
            transient_diff_excerpt="```\nnet change\n```",
        )
    )
    assert "net effect is non-empty" in md
    fences = _fence_lines(md)
    assert any(len(f) >= 4 for f in fences), f"transient diff not safely fenced:\n{md}"


# --- transient rendering ---------------------------------------------------

def test_transient_success(render):
    md = render(
        _doc(
            success=True,
            transient_commits=[_commit(1, subject="transient: temp")],
            transient_verified=True,
        )
    )
    assert "net effect: none" in md
    assert "✅" in md


def test_transient_cherry_pick_conflict(render):
    md = render(
        _doc(
            success=False,
            transient_commits=[_commit(1, subject="transient: temp")],
            transient_verified=False,
            transient_failure_kind="cherry_pick_conflict",
            transient_failed_short="abc1234",
            transient_failed_subject="feat: conflicting change",
            transient_output_excerpt="CONFLICT in A.lean",
        )
    )
    assert "cherry-pick conflict during replay" in md
    assert "abc1234" in md


# --- size cap --------------------------------------------------------------

def test_backtick_in_failed_subject_is_escaped(render):
    c = _auto_fail(1)
    c["subject"] = "x: run `dangerous` cmd"
    md = render(_doc(success=False, auto_commits=[c]))
    # The subject sits in a <code> span; backticks are escaped so the span renders.
    assert "run \\`dangerous\\` cmd" in md


def test_hard_cap_truncates_when_forced_low(render):
    md = render(
        _doc(success=True, substantive_commits=[_commit(i) for i in range(50)]),
        env_extra={"MAX_COMMENT_BYTES": 400, "SUBSTANTIVE_LIST_CAP": 50},
    )
    assert "comment truncated" in md
    assert len(md.encode()) <= 400
