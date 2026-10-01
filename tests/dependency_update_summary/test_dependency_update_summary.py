from __future__ import annotations

import re

import pytest

import dependency_update_summary as dus

URL = "https://github.com/leanprover-community/batteries"
BATTERIES = dus.GitHubRepo("leanprover-community", "batteries")


def run(capsys, old, new, extra=()):
    args = ["--old-manifest", str(old), "--new-manifest", str(new)]
    for e in extra:
        args += ["--extra", *e]
    assert dus.main(args) == 0
    return capsys.readouterr().out


def without_code_spans(markdown: str) -> str:
    """Remove every code span, with the delimiters `code_span` produces."""
    return re.sub(r"(`+) .*? \1", "", markdown)


# ---------------------------------------------------------------------------
# code_span


@pytest.mark.parametrize(
    "subject",
    [
        "plain subject",
        "has `code` inside",
        "has ``double`` and ```triple``` runs",
        "`starts and ends with backticks`",
        "@octocat please look",
        "<script>alert(1)</script>",
        "[click](javascript:alert(1))",
        "**bold** _it_ ~~del~~ :tada:",
        "fixes #12 and leanprover/lean4#34",
    ],
)
def test_code_span_contains_text_literally(subject):
    span = dus.code_span(subject)
    m = re.fullmatch(r"(`+) (.*) \1", span)
    assert m is not None
    assert m.group(2) == subject
    # No backtick run in the content is as long as the delimiter.
    longest = max((len(r) for r in re.findall(r"`+", subject)), default=0)
    assert len(m.group(1)) == longest + 1


def test_code_span_replaces_control_and_format_characters():
    # Newline, carriage return, tab, NUL, a right-to-left override, a
    # zero-width space and a line separator.
    span = dus.code_span("a\nb\rc\td\x00e‮f​g h")
    assert span == "` a b c d e f g h `"


def test_code_span_truncates():
    span = dus.code_span("x" * 1000)
    assert len(span) == dus.MAX_SUBJECT + 4
    assert span.endswith("… `")


def test_code_span_empty():
    assert dus.code_span(" \n\t ") == "*(no subject)*"


# ---------------------------------------------------------------------------
# issue_links


def test_issue_links_default_repo():
    assert dus.issue_links("fix: thing (#2000)", BATTERIES) == [
        "[#2000](https://redirect.github.com/leanprover-community/batteries/issues/2000)"
    ]


def test_issue_links_cross_repo():
    assert dus.issue_links("chore: adapt to leanprover/lean4#15141", BATTERIES) == [
        "[leanprover/lean4#15141]"
        "(https://redirect.github.com/leanprover/lean4/issues/15141)"
    ]


def test_issue_links_without_default_repo():
    assert dus.issue_links("fix (#1), see a/b#2", None) == [
        "[a/b#2](https://redirect.github.com/a/b/issues/2)"
    ]


def test_issue_links_deduplicates():
    assert len(dus.issue_links("#1 #1 (#1)", BATTERIES)) == 1


@pytest.mark.parametrize(
    "subject",
    [
        "foo#1",
        "a/b/c#1",
        "https://example.com/page#1",
        "##1",
        "-#1",
        "no references",
    ],
)
def test_issue_links_ignores_non_references(subject):
    assert dus.issue_links(subject, BATTERIES) == []


def test_issue_links_never_link_to_github_directly():
    links = dus.issue_links("#1 a/b#2 c/d#3", BATTERIES)
    assert links
    for link in links:
        assert "(https://redirect.github.com/" in link
        assert "(https://github.com/" not in link


# ---------------------------------------------------------------------------
# parse_github_url


@pytest.mark.parametrize(
    "url, expected",
    [
        (URL, BATTERIES),
        (URL + ".git", BATTERIES),
        (URL + "/", BATTERIES),
        ("https://github.com/leanprover/lean4-cli", dus.GitHubRepo("leanprover", "lean4-cli")),
    ],
)
def test_parse_github_url(url, expected):
    assert dus.parse_github_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://gitlab.com/a/b",
        "https://github.com/a",
        "https://github.com/a/b/c",
        "https://github.com/a/..",
        "https://github.com/a/b?x=1",
        "https://github.com/a/b#frag",
        "https://github.com/a/b)[x](y",
        "https://github.com/a/b\n",
        None,
        42,
    ],
)
def test_parse_github_url_rejects(url):
    assert dus.parse_github_url(url) is None


# ---------------------------------------------------------------------------
# end to end


def test_update_lists_commits_newest_first(capsys, make_repo, write_manifests):
    repo = make_repo("batteries")
    old = repo.commit("initial")
    a = repo.commit("feat: first (#1)")
    b = repo.commit("fix: second (#2)")
    out = run(capsys, *write_manifests({"batteries": (URL, old)}, {"batteries": (URL, b)}))
    assert out.startswith("### batteries\n")
    assert f"[`{old[:7]}...{b[:7]}`]({URL}/compare/{old}...{b}): 2 commits." in out
    lines = [l for l in out.splitlines() if l.startswith("- ")]
    assert lines == [
        f"- [`{b[:7]}`]({URL}/commit/{b}) ` fix: second (#2) ` "
        "([#2](https://redirect.github.com/leanprover-community/batteries/issues/2))",
        f"- [`{a[:7]}`]({URL}/commit/{a}) ` feat: first (#1) ` "
        "([#1](https://redirect.github.com/leanprover-community/batteries/issues/1))",
    ]


def test_unchanged_dependency_is_omitted(capsys, make_repo, write_manifests):
    repo = make_repo("batteries")
    rev = repo.commit("initial")
    out = run(capsys, *write_manifests({"batteries": (URL, rev)}, {"batteries": (URL, rev)}))
    assert out == "No dependency revision changes.\n"


def test_untrusted_subjects_stay_inside_code_spans(capsys, make_repo, write_manifests):
    repo = make_repo("batteries")
    old = repo.commit("initial")
    hostile = [
        "@octocat @leanprover-community/mathlib-maintainers",
        "<img src=x onerror=alert(1)> <!-- comment",
        "[link](https://evil.example) ![img](https://evil.example/x.png)",
        "``` fence\n\n### heading\n- item",
        "https://github.com/leanprover-community/mathlib4/issues/1",
        "close leanprover-community/mathlib4#1 GH-2",
        "a ` b `` c ``` d",
        "‮desrever‬",
    ]
    for subject in hostile:
        repo.commit(subject)
    new = repo.git("rev-parse", "HEAD")
    out = run(capsys, *write_manifests({"batteries": (URL, old)}, {"batteries": (URL, new)}))

    rest = without_code_spans(out)
    for needle in ["@", "<", "evil.example", "onerror", "GH-2", "‮", "```"]:
        assert needle not in rest
    # Each subject sits on its own list item.
    assert len([l for l in out.splitlines() if l.startswith("- ")]) == len(hostile)
    assert "\n### heading" not in out
    # The only links are the commit, comparison and redirect links.
    for target in re.findall(r"\]\(([^)]*)\)", rest):
        assert target.startswith(
            (f"{URL}/commit/", f"{URL}/compare/", "https://redirect.github.com/")
        ), target


def test_commit_list_is_capped(capsys, make_repo, write_manifests, monkeypatch):
    monkeypatch.setattr(dus, "MAX_COMMITS", 3)
    repo = make_repo("batteries")
    old = repo.commit("initial")
    for i in range(5):
        new = repo.commit(f"commit {i}")
    out = run(capsys, *write_manifests({"batteries": (URL, old)}, {"batteries": (URL, new)}))
    assert ": 5 commits." in out
    assert "` commit 4 `" in out and "` commit 2 `" in out
    assert "` commit 1 `" not in out
    assert "- 2 older commits not shown, see the comparison." in out


def test_output_is_capped(capsys, make_repo, write_manifests, monkeypatch):
    monkeypatch.setattr(dus, "MAX_OUTPUT", 200)
    repo = make_repo("batteries")
    old = repo.commit("initial")
    for i in range(5):
        new = repo.commit(f"commit {i}")
    out = run(capsys, *write_manifests({"batteries": (URL, old)}, {"batteries": (URL, new)}))
    assert ": 5 commits." in out
    assert "- " not in out
    assert "too long for the PR description" in out


def test_downgrade(capsys, make_repo, write_manifests):
    repo = make_repo("batteries")
    new = repo.commit("initial")
    old = repo.commit("later")
    out = run(capsys, *write_manifests({"batteries": (URL, old)}, {"batteries": (URL, new)}))
    assert "**the new revision is older than the old revision.**" in out
    assert "- " not in out


def test_diverged(capsys, make_repo, write_manifests):
    repo = make_repo("batteries")
    base = repo.commit("base")
    old = repo.commit("old side")
    repo.git("checkout", "-q", "-b", "other", base)
    new = repo.commit("new side")
    out = run(capsys, *write_manifests({"batteries": (URL, old)}, {"batteries": (URL, new)}))
    assert ": 1 commit. **The old revision is not an ancestor" in out
    assert "` new side `" in out
    assert "` old side `" not in out


def test_missing_revision(capsys, make_repo, write_manifests):
    repo = make_repo("batteries")
    new = repo.commit("initial")
    old = "0" * 40
    out = run(capsys, *write_manifests({"batteries": (URL, old)}, {"batteries": (URL, new)}))
    assert "a revision is missing from the local clone." in out


def test_missing_clone(capsys, write_manifests):
    out = run(capsys, *write_manifests({"batteries": (URL, "a" * 40)}, {"batteries": (URL, "b" * 40)}))
    assert "the local clone is not available." in out


def test_revision_not_a_hash(capsys, make_repo, write_manifests):
    repo = make_repo("batteries")
    old = repo.commit("initial")
    out = run(capsys, *write_manifests({"batteries": (URL, old)}, {"batteries": (URL, "main")}))
    assert "The new revision is not a commit hash." in out
    assert "/compare/" not in out


def test_added_and_removed(capsys, make_repo, write_manifests):
    repo = make_repo("newdep")
    rev = repo.commit("initial")
    old_url = "https://github.com/a/olddep"
    out = run(
        capsys,
        *write_manifests(
            {"olddep": (old_url, "c" * 40)},
            {"newdep": (URL, rev)},
        ),
    )
    assert f"This dependency is new, at [`{rev[:7]}`]({URL}/commit/{rev})." in out
    assert f"This dependency is removed. It was at [`ccccccc`]({old_url}/commit/{'c' * 40})." in out


def test_non_github_url_has_no_links(capsys, make_repo, write_manifests):
    repo = make_repo("batteries")
    old = repo.commit("initial")
    new = repo.commit("fix (#1)")
    url = "https://example.com/batteries](https://evil.example)"
    out = run(capsys, *write_manifests({"batteries": (url, old)}, {"batteries": (url, new)}))
    assert "](" not in out
    assert "evil.example" not in out


def test_hostile_package_name(capsys, write_manifests):
    name = "x](https://evil.example)"
    out = run(capsys, *write_manifests({name: (URL, "a" * 40)}, {name: (URL, "b" * 40)}))
    assert out.startswith("### ` x](https://evil.example) `\n")
    assert "evil.example" not in without_code_spans(out)
    assert "the local clone is not available." in out


def test_extra_repository(capsys, make_repo, write_manifests, tmp_path):
    repo = make_repo("mathlib-ci-clone")
    old = repo.commit("initial")
    new = repo.commit("fix: thing (#66)")
    ci_url = "https://github.com/leanprover-community/mathlib-ci"
    out = run(
        capsys,
        *write_manifests({}, {}),
        extra=[("mathlib-ci", ci_url, str(repo.path), old, new)],
    )
    assert out.startswith("### mathlib-ci\n")
    assert f"({ci_url}/compare/{old}...{new}): 1 commit." in out
    assert "redirect.github.com/leanprover-community/mathlib-ci/issues/66" in out


def test_extra_repository_unchanged(capsys, write_manifests):
    rev = "a" * 40
    out = run(
        capsys,
        *write_manifests({}, {}),
        extra=[("mathlib-ci", URL, "/nonexistent", rev, rev)],
    )
    assert out == "No dependency revision changes.\n"
