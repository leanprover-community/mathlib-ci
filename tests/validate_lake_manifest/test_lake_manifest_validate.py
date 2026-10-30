"""`validate()`, the report policy and the CLI, driven through `FakeClient`."""

from __future__ import annotations

import io

import pytest
from conftest import FORK, HEAD, MERGE_BASE, NEW, OLD, REPOSITORY, FakeClient, manifest_text, package

from lake_manifest.__main__ import main
from lake_manifest.github import ApiError
from lake_manifest.manifest import ManifestError
from lake_manifest.report import ADMIN_NOTE, OVERRIDE_NOTE, STRICT_NOTE, Annotation, Policy, annotate
from lake_manifest.rules import Finding, Kind
from lake_manifest.validate import validate

BASE = manifest_text(package(rev=OLD))


def kinds(client) -> list[Kind]:
    return [f.kind for f in validate(client, REPOSITORY, HEAD)]


def test_untouched_manifest_skips_the_pin_checks():
    client = FakeClient(head=manifest_text(package(rev=FORK)), touched=False)
    assert kinds(client) == []
    assert [c[0] for c in client.calls] == ["read_manifest", "compare"]


def test_compare_against_master_at_the_ref():
    client = FakeClient(head=BASE, base=BASE)
    validate(client, REPOSITORY, HEAD)
    assert ("compare", REPOSITORY, "master", HEAD) in client.calls
    assert ("read_manifest", REPOSITORY, MERGE_BASE) in client.calls


def test_full_file_list_may_omit_the_manifest():
    client = FakeClient(head=manifest_text(package(rev=FORK)), base=BASE, files=[{"filename": "x"}] * 300)
    assert kinds(client) == [Kind.PIN]


def test_field_findings_skip_the_pin_checks_for_that_dependency():
    client = FakeClient(head=manifest_text(package(rev="main"), package("aesop", rev=FORK)), base=BASE)
    assert kinds(client) == [Kind.FIELD, Kind.PIN]


def test_pin_check_errors_are_unchecked_findings():
    client = FakeClient(head=BASE, compare_error=502)
    [finding] = validate(client, REPOSITORY, HEAD)
    assert finding.kind is Kind.UNCHECKED
    assert finding.problem == "CI could not check the dependency pins: GET /compare failed with HTTP 502"


def test_unreadable_base_manifest_is_an_unchecked_finding():
    client = FakeClient(head=BASE, base="not json")
    assert kinds(client) == [Kind.UNCHECKED]


@pytest.mark.parametrize("error", [ApiError("/contents", 404), ManifestError("lake-manifest.json at `head` is a symlink, not a file.")])
def test_unreadable_manifest_raises(error):
    with pytest.raises(type(error)):
        validate(FakeClient(head=error), REPOSITORY, HEAD)


PIN = Finding(Kind.PIN, "t", "problem", hints=("Hint: fix.",))
OLDER = Finding(Kind.OLDER_PIN, "t", "problem", hints=("Hint: fix.",))
FIELD = Finding(Kind.FIELD, "t", "problem", notes=("Note: rule.",), hints=("Hint: fix.",))


@pytest.mark.parametrize("finding, policy, level, message", [
    (FIELD, Policy(False, False), "error", "problem\nNote: rule.\nHint: fix."),
    (PIN, Policy(False, False), "warning", f"problem\n{STRICT_NOTE}\nHint: fix."),
    (PIN, Policy(True, True), "error", f"problem\n{STRICT_NOTE}\nHint: fix."),
    (OLDER, Policy(True, False), "error", f"problem\n{STRICT_NOTE}\n{ADMIN_NOTE}\nHint: fix."),
    (OLDER, Policy(False, False), "warning", f"problem\n{STRICT_NOTE}\n{ADMIN_NOTE}\nHint: fix."),
    (OLDER, Policy(True, True), "warning", f"problem\n{OVERRIDE_NOTE}"),
], ids=["field", "pin", "pin-strict-override", "older-strict", "older", "older-override"])
def test_policy(finding, policy, level, message):
    assert annotate(finding, policy) == Annotation(level, "t", message)


def test_command_escapes_properties_and_data():
    command = Annotation("error", "a: b, c", "100%\nline\r").command()
    assert command == "::error file=lake-manifest.json,title=a%3A b%2C c::100%25%0Aline%0D"


def run(client, *args) -> tuple[int, list[str]]:
    out = io.StringIO()
    code = main(["--repository", REPOSITORY, "--ref", HEAD, *args], env={}, client=client, out=out)
    return code, out.getvalue().splitlines()


def test_main_passes_a_valid_manifest():
    assert run(FakeClient(head=BASE, base=BASE)) == (0, [])


def test_main_warns_without_strict():
    code, lines = run(FakeClient(head=manifest_text(package(rev=FORK)), base=BASE))
    assert code == 0
    assert [line.split(" ", 1)[0] for line in lines] == ["::warning"]


def test_main_fails_with_strict():
    code, lines = run(FakeClient(head=manifest_text(package(rev=FORK)), base=BASE), "--strict")
    assert code == 1
    assert lines[-1] == "::error::lake-manifest.json is not valid."


def test_main_override_relaxes_only_the_older_rule():
    head = manifest_text(package(rev=OLD), package("aesop", rev=FORK))
    base = manifest_text(package(rev=NEW), package("aesop", rev=OLD))
    code, lines = run(FakeClient(head=head, base=base), "--strict", "--allow-older-pins")
    assert code == 1
    assert [line.split(" ", 1)[0] for line in lines] == ["::warning", "::error", "::error::lake-manifest.json"]


def test_main_fails_when_the_manifest_cannot_be_read():
    code, lines = run(FakeClient(head=ApiError("/contents/lake-manifest.json", 404)))
    assert code == 1
    assert lines == ["::error::CI could not read lake-manifest.json: GET /contents/lake-manifest.json failed with HTTP 404"]
