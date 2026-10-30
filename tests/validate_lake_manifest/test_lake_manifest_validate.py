"""The parser, the rules and `validate()`, driven through `FakeGitHub`."""

from __future__ import annotations

import pytest
from conftest import FORK, HEAD, NEW, OLD, REPOSITORY, TAG, FakeGitHub, manifest_text, package

from lake_manifest.github import ApiError
from lake_manifest.validate import FIELD, OLDER_PIN, PIN, UNCHECKED, Dependency, Finding, check_fields, check_pin, parse, validate


def dep(**fields) -> Dependency:
    return parse(manifest_text(package(**fields)))[0]


def titles(findings: list[Finding]) -> list[str]:
    return [f.title for f in findings]


@pytest.mark.parametrize("text", ["", "{}", '{"packages": [1]}'])
def test_parse_rejects_a_manifest_without_packages(text):
    with pytest.raises(ValueError):
        parse(text)


def test_parse_maps_non_string_fields_to_none():
    parsed = parse(manifest_text({"name": "x", "url": 1, "inputRev": None}))[0]
    assert (parsed.url, parsed.input_rev, parsed.rev, parsed.inherited) == (None, None, None, False)


@pytest.mark.parametrize("input_rev", ["main", "master", "v4.25.0", "v4.25.0-rc1"])
def test_allowed_input_revs(input_rev):
    assert check_fields(dep(input_rev=input_rev)) == []


@pytest.mark.parametrize("input_rev", ["nightly-testing", "v4.25", "Main", None])
def test_disallowed_input_revs(input_rev):
    assert titles(check_fields(dep(input_rev=input_rev))) == ["Invalid dependency inputRev"]


@pytest.mark.parametrize("url", [
    "https://github.com/evil/batteries",
    "https://github.com/leanprover-community/batteries/../x",
    "http://github.com/leanprover-community/batteries",
    "https://gitlab.com/leanprover-community/batteries",
])
def test_disallowed_repositories(url):
    assert titles(check_fields(dep(url=url))) == ["Invalid dependency repository"]


def test_allowed_repository_in_leanprover():
    assert dep(url="https://github.com/leanprover/lean4-cli").repository == "leanprover/lean4-cli"


@pytest.mark.parametrize("rev", ["main", NEW[:12], "A" * 40, None])
def test_pin_must_be_a_full_hash(rev):
    assert titles(check_fields(dep(rev=rev))) == ["Invalid dependency pin"]


def test_every_broken_field_is_a_finding():
    findings = check_fields(dep(input_rev="nightly-testing", url="https://github.com/evil/b", rev="main"))
    assert titles(findings) == ["Invalid dependency inputRev", "Invalid dependency repository", "Invalid dependency pin"]


def test_require_hint_depends_on_inherited():
    [plain] = check_fields(dep(input_rev="x"))
    [inherited] = check_fields(dep(input_rev="x", inherited=True))
    assert "lakefile.lean" in plain.advice[-1]
    assert "the dependency that requires it" in inherited.advice[-1]


def pin_finding(new: Dependency, old: Dependency | None, **github) -> Finding | None:
    return check_pin(new, old, FakeGitHub(head=None, **github))


def test_unchanged_pin_is_not_checked():
    # FORK is on no branch, so a check would produce a finding.
    assert pin_finding(dep(rev=FORK), dep(rev=FORK)) is None


def test_newer_pin_on_the_branch():
    assert pin_finding(dep(rev=NEW), dep(rev=OLD)) is None


def test_pin_off_the_branch():
    assert pin_finding(dep(rev=FORK), dep(rev=OLD)).kind == PIN


def test_older_pin():
    assert pin_finding(dep(rev=OLD), dep(rev=NEW)).kind == OLDER_PIN


@pytest.mark.parametrize("old", [
    None,
    dep(rev=NEW, input_rev="master"),
    dep(rev=NEW, url="https://github.com/leanprover/batteries"),
], ids=["new-dependency", "new-input-rev", "new-url"])
def test_older_rule_needs_the_same_source(old):
    assert pin_finding(dep(rev=OLD), old) is None


def test_new_source_with_the_same_pin_is_still_checked():
    assert pin_finding(dep(rev=FORK), dep(rev=FORK, input_rev="master")).kind == PIN


def test_tag_pin():
    tags = {"v4.25.0": TAG}
    assert pin_finding(dep(input_rev="v4.25.0", rev=TAG), None, tags=tags) is None
    assert pin_finding(dep(input_rev="v4.25.0", rev=NEW), None, tags=tags).kind == PIN
    assert pin_finding(dep(input_rev="v4.25.0", rev=TAG), None).kind == PIN


BASE = manifest_text(package(rev=OLD))


def kinds(github: FakeGitHub) -> list[str]:
    return [f.kind for f in validate(github, REPOSITORY, HEAD)]


def test_field_findings_skip_the_pin_checks_for_that_dependency():
    head = manifest_text(package(rev="main"), package("aesop", rev=FORK))
    assert kinds(FakeGitHub(head=head, base=BASE)) == [FIELD, PIN]


@pytest.mark.parametrize("base", [ApiError("GET /x failed", 502), "not json"], ids=["api-error", "unreadable"])
def test_pin_check_failures_are_unchecked_findings(base):
    assert kinds(FakeGitHub(head=BASE, base=base)) == [UNCHECKED]


@pytest.mark.parametrize("head, error", [(ApiError("GET /x failed", 404), ApiError), ("not json", ValueError)])
def test_unreadable_manifest_raises(head, error):
    with pytest.raises(error):
        validate(FakeGitHub(head=head), REPOSITORY, HEAD)
