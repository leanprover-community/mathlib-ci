"""Tests for updateDeclsDiffSection's marker-based splicing."""

from __future__ import annotations

from declsDiff import render_override
from updateDeclsDiffSection import (
    LEAN_BEGIN,
    LEAN_END,
    PR_SUMMARY_PREFIX,
    SUMMARY_COMMENT_AUTHOR,
    build_warning,
    carry_forward,
    find_summary_comment,
    splice,
)

BOT = {"login": SUMMARY_COMMENT_AUTHOR}
HUMAN = {"login": "not-the-bot"}

# A success block as produced by `declsDiff.py --with-heading`.
SECTION = "#### Declarations diff (Lean)\n\n> ✅ **Lean-aware diff**\n\n* **+1** new declarations\n"

STALE_HEADING = "#### Declarations diff (Lean -- stale, waiting for the new build)"
MISS_HEADING = "#### Declarations diff (Lean -- stale; merge master and push to refresh)"


def comment(inner: str) -> str:
    """A minimal `### PR summary` comment carrying the regex block plus a Lean
    region whose contents are `inner`."""
    return (
        f"{PR_SUMMARY_PREFIX} [abc1234](url)\n\n"
        "#### Import changes for modified files\n\nfoo\n\n---\n\n"
        "#### Declarations diff (regex)\n\n`+ Foo`\n\n---\n\n"
        f"{LEAN_BEGIN}\n{inner}\n{LEAN_END}\n\n---\n\n"
        "No changes to technical debt.\n"
    )


PLACEHOLDER = "#### Declarations diff (Lean -- pending)\n\n_Computed after the build finishes._"


class TestSplice:
    def test_replaces_region_content(self) -> None:
        """The placeholder is gone and the new section is spliced in."""
        body = splice(comment(PLACEHOLDER), SECTION)
        assert "pending" not in body
        assert "**+1** new declarations" in body
        assert "#### Declarations diff (Lean)" in body

    def test_keeps_regex_block_and_markers(self) -> None:
        """Splicing touches only the Lean region; the regex block and markers survive."""
        body = splice(comment(PLACEHOLDER), SECTION)
        assert "#### Declarations diff (regex)" in body
        assert "`+ Foo`" in body
        assert "#### Import changes for modified files" in body
        assert body.count(LEAN_BEGIN) == 1 and body.count(LEAN_END) == 1

    def test_idempotent(self) -> None:
        """Re-splicing the same block is a no-op on the already-patched body."""
        once = splice(comment(PLACEHOLDER), SECTION)
        assert splice(once, SECTION) == once

    def test_noop_without_markers(self) -> None:
        """A comment lacking the Lean markers is returned byte-for-byte unchanged."""
        body = f"{PR_SUMMARY_PREFIX}\n\n#### Declarations diff (regex)\n\nlegacy\n"
        assert splice(body, SECTION) == body

    def test_summary_prefix_preserved(self) -> None:
        """Guardrail: the leading `### PR summary` line (used to locate the
        comment) is never rewritten by a patch."""
        assert splice(comment(PLACEHOLDER), SECTION).startswith(PR_SUMMARY_PREFIX)


class TestWarning:
    def test_unavailable_heading_and_keeps_regex(self) -> None:
        """Warning mode shows the `(Lean -- unavailable)` status and leaves the
        regex block intact."""
        body = splice(comment(PLACEHOLDER), build_warning("master"))
        assert "#### Declarations diff (Lean -- unavailable)" in body
        assert "Merge `master`" in body
        assert "#### Declarations diff (regex)" in body
        assert "`+ Foo`" in body

    def test_idempotent(self) -> None:
        once = splice(comment(PLACEHOLDER), build_warning("master"))
        assert splice(once, build_warning("master")) == once

    def test_summary_prefix_preserved(self) -> None:
        assert splice(comment(PLACEHOLDER), build_warning("master")).startswith(PR_SUMMARY_PREFIX)


class TestCarryForward:
    def test_real_diff_relabelled_body_kept(self) -> None:
        """A real diff is carried forward: heading swapped, stamp and counts kept."""
        out = carry_forward(SECTION, STALE_HEADING)
        assert out is not None
        assert out.startswith(STALE_HEADING)
        assert "✅ **Lean-aware diff**" in out
        assert "**+1** new declarations" in out
        assert "(Lean -- pending)" not in out

    def test_survives_repeated_relabels(self) -> None:
        """Keyed on the body stamp (not the heading), so a stale diff can be
        relabelled again (e.g. on a later cache miss) without being lost."""
        stale = carry_forward(SECTION, STALE_HEADING)
        miss = carry_forward(stale, MISS_HEADING)
        assert miss is not None
        assert miss.startswith(MISS_HEADING)
        assert "**+1** new declarations" in miss

    def test_pending_not_carryable(self) -> None:
        """The pending placeholder has no diff stamp, so there is nothing to keep."""
        assert carry_forward(PLACEHOLDER, STALE_HEADING) is None

    def test_empty_heading_never_carries(self) -> None:
        assert carry_forward(SECTION, "") is None

    def test_warning_keeps_prior_diff(self) -> None:
        """Warning mode carries a prior real diff forward under the cache-miss
        heading instead of blanking it to `unavailable`."""
        prior = splice(comment(PLACEHOLDER), SECTION)
        kept = carry_forward(SECTION, MISS_HEADING)
        assert kept is not None
        body = splice(prior, kept)
        assert MISS_HEADING in body
        assert "**+1** new declarations" in body
        assert "(Lean -- unavailable)" not in body

    def test_warning_falls_back_when_no_prior_diff(self) -> None:
        """With only the pending placeholder present, warning mode still shows
        the unavailable block."""
        assert carry_forward(PLACEHOLDER, MISS_HEADING) is None
        body = splice(comment(PLACEHOLDER), build_warning("master"))
        assert "(Lean -- unavailable)" in body

    def test_real_producer_output_is_carryable(self) -> None:
        """Contract guard against stamp drift: a section rendered by the *real*
        producer (`render_override`, the same code path the post-build action
        runs) must satisfy `carry_forward`. The hand-written `SECTION` fixture
        cannot catch a reworded stamp in declsDiff.py — this can."""
        produced = render_override(
            plus=1, minus=0, diff=[("+", "Foo.bar")],
            head_sha="abc1234def", with_heading=True,
        )
        assert carry_forward(produced, STALE_HEADING) is not None

    def test_heading_with_regex_metachars_is_literal(self) -> None:
        """A heading containing backslashes is substituted literally, not as a
        regex replacement template."""
        out = carry_forward(SECTION, r"#### Declarations diff (Lean \g<0> \1)")
        assert r"\g<0>" in out


class TestFindSummaryComment:
    def test_finds_the_summary_among_others(self) -> None:
        """The bot's `### PR summary` comment is picked out of a list of comments."""
        comments = [
            {"body": "a normal review comment", "user": HUMAN},
            {"body": f"{PR_SUMMARY_PREFIX} [abc](url)\n\nbody", "user": BOT},
            {"body": "another comment", "user": HUMAN},
        ]
        assert find_summary_comment(comments)["body"].startswith(PR_SUMMARY_PREFIX)

    def test_returns_none_when_absent(self) -> None:
        assert find_summary_comment([{"body": "nope", "user": BOT}]) is None
        assert find_summary_comment([]) is None

    def test_tolerates_null_body(self) -> None:
        """A comment with a `null` body (allowed by the API) is skipped, not fatal."""
        comments = [
            {"body": None, "user": BOT},
            {"body": f"{PR_SUMMARY_PREFIX}\n\nbody", "user": BOT},
        ]
        assert find_summary_comment(comments)["body"].startswith(PR_SUMMARY_PREFIX)

    def test_ignores_non_bot_impostor(self) -> None:
        """A non-bot comment starting with the prefix is skipped in favour of the
        bot's — so attacker-authored region content is never read or carried forward."""
        comments = [
            {"body": f"{PR_SUMMARY_PREFIX}\n\n{LEAN_BEGIN}fake{LEAN_END}", "user": HUMAN},
            {"body": f"{PR_SUMMARY_PREFIX} [real](url)\n\nbody", "user": BOT},
        ]
        sel = find_summary_comment(comments)
        assert sel is not None and sel["user"]["login"] == SUMMARY_COMMENT_AUTHOR

    def test_tolerates_missing_user(self) -> None:
        """A comment with no `user` key is skipped, not fatal."""
        assert find_summary_comment([{"body": f"{PR_SUMMARY_PREFIX}\n\nx"}]) is None
