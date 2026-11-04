"""Tests for the matching and rendering in `related_dependency_prs.py`.

The cases are modelled on real mathlib4 PRs: #45113 (a ProofWidgets bump to the bot's revision),
#44264 (an import-graph adaptation that does not touch the manifest), and the
`batteries-pr-testing-N` PRs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from related_dependency_prs import (
    MARKER,
    Bump,
    Candidate,
    adaptation_style_title,
    bumps_between,
    classify,
    code_span,
    package_names,
    plan,
    posted_from_comment,
    referenced_upstream_prs,
    render_comment,
    render_zulip,
    upstream_pr_numbers,
)

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
RECENT = NOW - timedelta(days=1)
STALE = NOW - timedelta(days=20)

PW = Bump("proofwidgets", "https://github.com/leanprover-community/ProofWidgets4", "ff78fbc", "eef6bb9")
IG = Bump("importGraph", "https://github.com/leanprover-community/import-graph", "aaa", "bbb")
BATT = Bump("batteries", "https://github.com/leanprover-community/batteries", "ccc", "ddd")
QQ = Bump("Qq", "https://github.com/leanprover-community/quote4", "eee", "fff")


def manifest(**revs: str) -> str:
    urls = {"batteries": BATT.url, "proofwidgets": PW.url, "importGraph": IG.url}
    return json.dumps({"version": "1.1.0", "packages": [
        {"type": "git", "name": name, "url": urls[name], "rev": rev} for name, rev in revs.items()
    ]})


def pr(number=1, title="chore: something", branch="some-branch", paths=(), last_commit=RECENT, changes=None):
    return Candidate(number, title, branch, set(paths), last_commit, manifest_changes=changes)


# ── names and title rules ────────────────────────────────────────────────────


def test_package_names_include_repo_name():
    assert set(package_names("Qq", QQ.url)) == {"qq", "quote4"}
    assert set(package_names("importGraph", IG.url)) == {"importgraph", "import-graph", "import graph"}
    assert set(package_names("proofwidgets", PW.url)) == {"proofwidgets", "proofwidgets4"}


def test_referenced_upstream_prs():
    names = BATT.names
    assert referenced_upstream_prs("anything", "batteries-pr-testing-2005", names) == {2005}
    assert referenced_upstream_prs("chore: adaptations for batteries#2005", "x", names) == {2005}
    assert referenced_upstream_prs("fix: adaptations for batteries #1927", "x", names) == {1927}
    assert referenced_upstream_prs(
        "adapt to widget changes from leanprover-community/import-graph#141", "x", IG.names) == {141}
    assert referenced_upstream_prs("see https://github.com/leanprover-community/quote4/pull/146", "x", QQ.names) == {146}
    assert referenced_upstream_prs("chore: adaptations for quote4#134", "qq-146-testing", QQ.names) == {134, 146}
    # another repository's PR, or a mathlib4 PR
    assert referenced_upstream_prs("chore: adaptations for aesop#12", "x", names) == set()
    assert referenced_upstream_prs("follow-up to #2005", "x", names) == set()


def test_adaptation_style_title():
    assert adaptation_style_title("chore: bump ProofWidgets to v0.0.118", "x", PW.names)
    assert adaptation_style_title("chore: update batteries for generalised `on_goal` handling", "x", BATT.names)
    assert adaptation_style_title("chore: adaptations for Aesop import reduction", "x", ["aesop"])
    assert not adaptation_style_title("perf: replace `aesop` proof with explicit term", "x", ["aesop"])
    assert not adaptation_style_title("chore: reduce some Batteries imports", "x", BATT.names)
    # the name has to be a whole word
    assert not adaptation_style_title("chore: update batteriesX", "x", BATT.names)


# ── manifest and upstream parsing ────────────────────────────────────────────


def test_bumps_between():
    bumps = bumps_between(manifest(batteries="1", proofwidgets="2"), manifest(batteries="1", proofwidgets="3"))
    assert bumps == [Bump("proofwidgets", PW.url, "2", "3")]


def test_upstream_pr_numbers_reads_squash_subjects():
    messages = ["chore: make ProofWidgets.Html protected (#205)\n\nbody (#7)", "Merge branch 'main'", "fix (#12) typo", ""]
    assert upstream_pr_numbers(messages) == {205}


# ── classification ───────────────────────────────────────────────────────────


def test_same_revision_is_likely():
    # #45113 against bot PR #45110
    m = classify(pr(45113, "chore: bump ProofWidgets to v0.0.118", paths={"lake-manifest.json"},
                    changes={"proofwidgets": "eef6bb9"}), [PW], {}, {}, NOW)
    assert m.likely and m.reasons == ["bumps `proofwidgets` to the same revision"]


def test_part_of_the_bump_is_likely():
    m = classify(pr(45121, "chore: update Mathlib dependencies", paths={"lake-manifest.json"},
                    changes={"proofwidgets": PW.new}), [PW, BATT], {}, {}, NOW)
    assert m.likely


def test_same_revision_by_accident_is_not_likely():
    # #44688 (batteries-pr-testing-2017) ran `lake update`, so it has the bot's aesop revision,
    # but its batteries entry still points at the unmerged batteries#2017
    aesop = Bump("aesop", "https://github.com/leanprover-community/aesop", "cbdde3a", "29c64c5")
    p = pr(44688, "chore: adaptations for batteries#2017", "batteries-pr-testing-2017",
           paths={"lake-manifest.json"}, changes={"aesop": "29c64c5", "batteries": "fork-rev"})
    m = classify(p, [aesop, BATT], {"aesop": set(), "batteries": {2005}}, {}, NOW)
    assert not m.likely
    assert m.reasons == ["also changes `aesop` in `lake-manifest.json`",
                         "also changes `batteries` in `lake-manifest.json`"]


def test_title_reference_to_included_upstream_pr_is_likely_without_manifest_change():
    # #44264 against bot PR #44246
    title = "chore(Tactic/Linter/UpstreamableDecl): adapt to widget changes from leanprover-community/import-graph#141"
    m = classify(pr(44264, title), [IG], {"importGraph": {139, 141}}, {}, NOW)
    assert m.likely and m.reasons == ["adapts import-graph#141, which this update includes"]


def test_reference_to_upstream_pr_not_in_the_bump_is_not_likely():
    # an adaptation waiting for an unmerged upstream PR (e.g. #44688 for batteries#2017)
    p = pr(44688, "chore: adaptations for batteries#2017", "batteries-pr-testing-2017",
           paths={"lake-manifest.json"}, changes={"batteries": "fork-rev"})
    m = classify(p, [BATT], {"batteries": {2005}}, {}, NOW)
    assert not m.likely and m.reasons == ["also changes `batteries` in `lake-manifest.json`"]
    p.last_commit = STALE
    assert classify(p, [BATT], {"batteries": {2005}}, {}, NOW) is None


def test_link_counts_only_with_manifest_change_or_styled_title():
    links = {"batteries": {44177: {2028}, 44498: {2005}}}
    included = {"batteries": {2005, 2028}}
    # a PR that only mentions a batteries PR in its description
    assert classify(pr(44177, "ci: lint local linters on only the modules a PR changes"),
                    [BATT], included, links, NOW) is None
    m = classify(pr(44498, "chore: tidy", paths={"lake-manifest.json"}, changes={"batteries": "fork"}),
                 [BATT], included, links, NOW)
    assert m.likely and m.reasons == ["adapts batteries#2005, which this update includes"]


def test_toolchain_bumps_are_skipped():
    p = pr(44879, "chore: bump toolchain to v4.36.0-rc1", paths={"lake-manifest.json", "lean-toolchain"},
           changes={"batteries": BATT.new})
    assert classify(p, [BATT], {}, {}, NOW) is None


def test_unrelated_prs_are_skipped():
    assert classify(pr(1, "perf: replace `aesop` proof"), [BATT, PW], {}, {}, NOW) is None
    # touches the manifest but not a bumped package
    assert classify(pr(2, "feat: x", paths={"lake-manifest.json"}, changes={"aesop": "1"}), [BATT], {}, {}, NOW) is None


def test_stale_styled_title_is_skipped_but_stale_likely_is_kept():
    assert classify(pr(3, "chore: bump batteries", last_commit=STALE), [BATT], {}, {}, NOW) is None
    m = classify(pr(4, "chore: bump batteries", paths={"lake-manifest.json"}, last_commit=STALE,
                    changes={"batteries": BATT.new}), [BATT], {}, {}, NOW)
    assert m.likely


# ── rendering and state ──────────────────────────────────────────────────────


def test_code_span_neutralises_titles():
    assert code_span("feat: `foo` for @**all** and @user") == "`feat: 'foo' for @**all** and @user`"
    assert code_span("a\nb") == "`a b`"
    assert len(code_span("x" * 500)) == 122


def test_comment_round_trips_posted_state():
    likely = classify(pr(45113, "chore: bump ProofWidgets", paths={"lake-manifest.json"},
                         changes={"proofwidgets": PW.new}), [PW], {}, {}, NOW)
    also = classify(pr(45200, "chore: update proofwidgets", paths={"lake-manifest.json"},
                       changes={"proofwidgets": "other"}), [PW], {}, {}, NOW)
    body = render_comment([likely, also], {45113, 1})
    assert body.startswith(MARKER)
    assert "* #45113 `chore: bump ProofWidgets`: bumps `proofwidgets` to the same revision" in body
    assert "**Also changing these dependencies**" in body and "* #45200" in body
    assert posted_from_comment(body) == {1, 45113}
    assert posted_from_comment(None) == set()
    assert "None at the moment." in render_comment([], set())


def test_zulip_message():
    assert render_zulip("o/r", 45110, []) == ""
    m = classify(pr(45113, "chore: bump `ProofWidgets`", paths={"lake-manifest.json"},
                    changes={"proofwidgets": PW.new}), [PW], {}, {}, NOW)
    text = render_zulip("leanprover-community/mathlib4", 45110, [m])
    assert "[#45110](https://github.com/leanprover-community/mathlib4/pull/45110)" in text
    assert "* [#45113](https://github.com/leanprover-community/mathlib4/pull/45113) `chore: bump 'ProofWidgets'`" in text


def test_plan_posts_each_likely_adaptation_once():
    repo = "leanprover-community/mathlib4"
    likely = classify(pr(45113, "chore: bump ProofWidgets", paths={"lake-manifest.json"},
                         changes={"proofwidgets": PW.new}), [PW], {}, {}, NOW)
    # nothing found, no comment yet: nothing to do
    assert plan(repo, 45110, [], None) == ("", "")
    # first sighting: post it and remember it
    body, zulip = plan(repo, 45110, [likely], None)
    assert "#45113" in zulip and posted_from_comment(body) == {45113}
    # next hour, same state: leave the comment alone, post nothing
    assert plan(repo, 45110, [likely], body) == ("", "")
    # GitHub may hand the body back with CRLF line endings
    assert plan(repo, 45110, [likely], body.replace("\n", "\r\n")) == ("", "")
    # it merges or closes: the comment says so, and it is never posted again
    body2, zulip2 = plan(repo, 45110, [], body)
    assert zulip2 == "" and "None at the moment." in body2 and posted_from_comment(body2) == {45113}
    assert plan(repo, 45110, [likely], body2)[1] == ""
