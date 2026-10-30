"""The manifest parser and the rules in `rules.py`."""

from __future__ import annotations

import pytest
from conftest import FORK, NEW, OLD, TAG, FakeClient, manifest_text, package

from lake_manifest import manifest, rules
from lake_manifest.manifest import Dependency, ManifestError
from lake_manifest.rules import Kind


def dep(**fields) -> Dependency:
    return manifest.parse(manifest_text(package(**fields)))[0]


@pytest.mark.parametrize("text", ["", "[]", '{"packages": 1}', '{"packages": [1]}', "{}"])
def test_parse_rejects_a_manifest_without_packages(text):
    with pytest.raises(ManifestError):
        manifest.parse(text)


def test_parse_maps_non_string_fields_to_none():
    parsed = manifest.parse(manifest_text({"name": "x", "url": 1, "inputRev": None}))[0]
    assert (parsed.url, parsed.input_rev, parsed.rev, parsed.inherited) == (None, None, None, False)


@pytest.mark.parametrize("input_rev", ["main", "master", "v4.25.0", "v4.25.0-rc1", "v1.2.3+build.5"])
def test_allowed_input_revs(input_rev):
    assert rules.check_fields(dep(input_rev=input_rev)) == []


@pytest.mark.parametrize("input_rev", ["nightly-testing", "v4.25", "4.25.0", "main\n", "Main"])
def test_disallowed_input_revs(input_rev):
    [finding] = rules.check_fields(dep(input_rev=input_rev))
    assert finding.kind is Kind.FIELD
    assert finding.title == "Invalid dependency inputRev"
    assert f"track `{input_rev}`" in finding.problem


def test_missing_input_rev():
    [finding] = rules.check_fields(dep(input_rev=None))
    assert finding.problem == "lake-manifest.json sets no `inputRev` for `batteries`."


@pytest.mark.parametrize("url", [
    "https://github.com/evil/batteries",
    "https://github.com/leanprover-community/batteries.git/../x",
    "http://github.com/leanprover-community/batteries",
    "https://github.com/leanprover-community/batteries/",
    "https://gitlab.com/leanprover-community/batteries",
])
def test_disallowed_repositories(url):
    [finding] = rules.check_fields(dep(url=url))
    assert finding.title == "Invalid dependency repository"


def test_allowed_repository_in_leanprover():
    assert dep(url="https://github.com/leanprover/lean4-cli").repository == ("leanprover", "lean4-cli")


@pytest.mark.parametrize("rev", ["main", NEW[:12], "A" * 40, None])
def test_pin_must_be_a_full_hash(rev):
    [finding] = rules.check_fields(dep(rev=rev))
    assert finding.title == "Invalid dependency pin"
    assert finding.hints == ("Hint: Run `lake update batteries` and commit lake-manifest.json.",)


def test_every_broken_field_is_a_finding():
    findings = rules.check_fields(dep(input_rev="nightly-testing", url="https://github.com/evil/b", rev="main"))
    assert [f.title for f in findings] == [
        "Invalid dependency inputRev", "Invalid dependency repository", "Invalid dependency pin"]


def test_require_hint_names_the_requiring_dependency_for_an_inherited_one():
    [finding] = rules.check_fields(dep(input_rev="x", inherited=True))
    assert finding.hints == (
        "Hint: Fix the `require` for `batteries` in the dependency that requires it, then run `lake update`.",)
    [finding] = rules.check_fields(dep(input_rev="x"))
    assert finding.hints == ("Hint: Fix the `require` for `batteries` in lakefile.lean, then run `lake update batteries`.",)


def check_pin(new: Dependency, old: Dependency | None, **client):
    return rules.check_pin(new, old, FakeClient(head=None, **client))


def test_unchanged_pin_makes_no_call():
    client = FakeClient(head=None)
    assert rules.check_pin(dep(), dep(), client) is None
    assert client.calls == []


def test_newer_pin_on_the_branch():
    assert check_pin(dep(rev=NEW), dep(rev=OLD)) is None


def test_pin_off_the_branch():
    finding = check_pin(dep(rev=FORK), dep(rev=OLD))
    assert finding.kind is Kind.PIN
    assert finding.title == "Dependency pin is not on branch main"
    assert f"`{FORK}`, which is not on the `main` branch of https://github.com/leanprover-community/batteries." in finding.problem


def test_older_pin():
    finding = check_pin(dep(rev=OLD), dep(rev=NEW))
    assert finding.kind is Kind.OLDER_PIN
    assert finding.problem.endswith(f"which does not descend from `{NEW}`, the pin on master.")


@pytest.mark.parametrize("old", [
    None,
    dep(rev=NEW, input_rev="master"),
    dep(rev=NEW, url="https://github.com/leanprover/batteries"),
], ids=["new-dependency", "new-input-rev", "new-url"])
def test_older_rule_needs_the_same_source(old):
    assert check_pin(dep(rev=OLD), old) is None


def test_new_source_with_the_same_pin_is_still_checked():
    finding = check_pin(dep(rev=FORK), dep(rev=FORK, input_rev="master"))
    assert finding.kind is Kind.PIN


def test_tag_pin():
    tagged = dep(input_rev="v4.25.0", rev=TAG)
    assert check_pin(tagged, None, tags={"v4.25.0": TAG}) is None
    finding = check_pin(dep(input_rev="v4.25.0", rev=NEW), None, tags={"v4.25.0": TAG})
    assert finding.title == "Dependency pin does not match tag v4.25.0"
    assert finding.problem.endswith(f"points to `{TAG}`.")
    finding = check_pin(tagged, None)
    assert finding.problem.endswith("does not exist.")
