"""End-to-end scenarios: the action's CLI against the fake GitHub API."""

from __future__ import annotations

import pytest
from conftest import FORK, NEW, OLD, TAG, UNKNOWN, FakeGitHub, manifest, package

BASE = manifest(package(rev=OLD))
NEWER = manifest(package(rev=NEW))
OFF_BRANCH = manifest(package(rev=FORK))
INVALID = ("error", "lake-manifest.json is not valid.")
NOT_ON_BRANCH = "Dependency pin is not on branch main"


def test_unchanged_manifest_passes(run):
    assert run(FakeGitHub(head=BASE, base=BASE)) == (0, [])


def test_newer_pin_on_the_branch_passes(run):
    assert run(FakeGitHub(head=NEWER, base=BASE)) == (0, [])


@pytest.mark.parametrize("fields", [
    {"input_rev": "master"}, {"input_rev": "v4.25.0"}, {"input_rev": "v4.25.0-rc1"},
    {"url": "https://github.com/leanprover/batteries"},
])
def test_allowed_fields(run, fields):
    text = manifest(package(**fields))
    assert run(FakeGitHub(head=text, base=text)) == (0, [])


@pytest.mark.parametrize("fields, title", [
    ({"input_rev": "nightly-testing"}, "Invalid dependency inputRev"),
    ({"input_rev": "v4.25"}, "Invalid dependency inputRev"),
    ({"input_rev": "Main"}, "Invalid dependency inputRev"),
    ({"input_rev": None}, "Invalid dependency inputRev"),
    ({"url": "https://github.com/evil/batteries"}, "Invalid dependency repository"),
    ({"url": "https://github.com/leanprover-community/batteries/../x"}, "Invalid dependency repository"),
    ({"url": "http://github.com/leanprover-community/batteries"}, "Invalid dependency repository"),
    ({"url": "https://gitlab.com/leanprover-community/batteries"}, "Invalid dependency repository"),
    ({"rev": "main"}, "Invalid dependency pin"),
    ({"rev": NEW[:12]}, "Invalid dependency pin"),
    ({"rev": "A" * 40}, "Invalid dependency pin"),
    ({"rev": None}, "Invalid dependency pin"),
])
def test_field_rules_fail_every_run(run, fields, title):
    assert run(FakeGitHub(head=manifest(package(**fields)), base=BASE)) == (1, [("error", title), INVALID])


def test_every_broken_field_is_reported(run):
    head = manifest(package(input_rev="nightly-testing", url="https://github.com/evil/b", rev="main"))
    assert run(FakeGitHub(head=head, base=BASE)) == (1, [
        ("error", "Invalid dependency inputRev"),
        ("error", "Invalid dependency repository"),
        ("error", "Invalid dependency pin"),
        INVALID,
    ])


def test_inherited_dependency_hint_names_the_requiring_dependency(stdout):
    _, out = stdout(FakeGitHub(head=manifest(package(input_rev="x", inherited=True)), base=BASE))
    assert "in the dependency that requires it" in out


def test_pin_off_the_branch(run):
    github = FakeGitHub(head=OFF_BRANCH, base=BASE)
    assert run(github) == (0, [("warning", NOT_ON_BRANCH)])
    assert run(github, "--strict") == (1, [("error", NOT_ON_BRANCH), INVALID])
    assert run(github, "--strict", "--allow-older-pins") == (1, [("error", NOT_ON_BRANCH), INVALID])


def test_unknown_commit_is_off_the_branch(run):
    assert run(FakeGitHub(head=manifest(package(rev=UNKNOWN)), base=BASE)) == (0, [("warning", NOT_ON_BRANCH)])


def test_older_pin(run):
    github = FakeGitHub(head=BASE, base=NEWER)
    assert run(github) == (0, [("warning", "Older dependency pin")])
    assert run(github, "--strict") == (1, [("error", "Older dependency pin"), INVALID])
    assert run(github, "--strict", "--allow-older-pins") == (0, [("warning", "Older dependency pin")])


@pytest.mark.parametrize("base", [
    manifest(),
    manifest(package(rev=NEW, input_rev="master")),
    manifest(package(rev=NEW, url="https://github.com/leanprover/batteries")),
], ids=["new-dependency", "new-input-rev", "new-url"])
def test_older_rule_needs_the_same_source(run, base):
    assert run(FakeGitHub(head=BASE, base=base)) == (0, [])


def test_new_source_with_the_same_pin_is_still_checked(run):
    base = manifest(package(rev=FORK, input_rev="master"))
    assert run(FakeGitHub(head=OFF_BRANCH, base=base)) == (0, [("warning", NOT_ON_BRANCH)])


def test_tag_pin(run):
    tags = {"v4.25.0": TAG}
    mismatch = ("warning", "Dependency pin does not match tag v4.25.0")
    assert run(FakeGitHub(head=manifest(package(input_rev="v4.25.0", rev=TAG)), base=BASE, tags=tags)) == (0, [])
    assert run(FakeGitHub(head=manifest(package(input_rev="v4.25.0", rev=NEW)), base=BASE, tags=tags)) == (0, [mismatch])
    assert run(FakeGitHub(head=manifest(package(input_rev="v4.25.0", rev=TAG)), base=BASE)) == (0, [mismatch])


def test_findings_follow_manifest_order(run):
    head = manifest(package(rev="main"), package("aesop", rev=FORK))
    assert run(FakeGitHub(head=head, base=BASE)) == (1, [
        ("error", "Invalid dependency pin"),
        ("warning", NOT_ON_BRANCH),
        INVALID,
    ])


@pytest.mark.parametrize("base", [404, "not json"], ids=["missing", "unreadable"])
def test_unreadable_base_manifest_is_unchecked(run, base):
    github = FakeGitHub(head=BASE, base=base)
    assert run(github) == (0, [("warning", "Dependency pin check failed")])
    assert run(github, "--strict") == (1, [("error", "Dependency pin check failed"), INVALID])


@pytest.mark.parametrize("head", [404, [], {"type": "symlink"}, "not json"],
                         ids=["missing", "directory", "symlink", "unreadable"])
def test_unreadable_manifest_fails(stdout, head):
    code, out = stdout(FakeGitHub(head=head))
    assert code == 1
    assert out.startswith("::error::CI could not read lake-manifest.json: ")


def test_transient_error_is_retried(run):
    # One retry, with its one-second backoff.
    assert run(FakeGitHub(head=BASE, base=BASE, failures=1)) == (0, [])


def test_annotation_carries_the_notes_and_the_hint(stdout):
    _, out = stdout(FakeGitHub(head=BASE, base=NEWER), "--strict")
    assert out.splitlines()[0] == (
        "::error file=lake-manifest.json,title=Older dependency pin::"
        f"lake-manifest.json pins `batteries` to `{OLD}`, which does not descend from `{NEW}`, the pin on master."
        "%0ANote: This check fails the Build job on bors runs."
        "%0ANote: For an intentional revert, a repository admin can set the variable "
        "`MATHLIB_ALLOW_OLDER_DEPENDENCY_PINS` to `true`."
        "%0AHint: Run `lake update batteries` and commit lake-manifest.json."
    )


def test_override_note_replaces_the_advice(stdout):
    _, out = stdout(FakeGitHub(head=BASE, base=NEWER), "--strict", "--allow-older-pins")
    assert out.splitlines()[0].endswith(
        "%0ANote: The variable `MATHLIB_ALLOW_OLDER_DEPENDENCY_PINS` is `true`, so this check only warns.")
