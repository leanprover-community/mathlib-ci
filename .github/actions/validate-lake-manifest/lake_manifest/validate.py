"""Read the manifests and apply the rules."""

from __future__ import annotations

from typing import Protocol

from . import manifest, rules
from .github import ApiError
from .manifest import Dependency, ManifestError
from .rules import Finding

BASE_BRANCH = "master"
# The compare API lists at most this many files, so a full list can omit lake-manifest.json.
COMPARE_FILE_LIMIT = 300


class Client(rules.Commits, Protocol):
    def compare(self, repository: str, base: str, head: str) -> dict: ...

    def read_manifest(self, repository: str, ref: str) -> str: ...


def validate(client: Client, repository: str, ref: str) -> list[Finding]:
    """The findings for lake-manifest.json at `ref`.

    Raises `ApiError` or `ManifestError` when the manifest at `ref` cannot
    be read. A failure in the pin checks is an `UNCHECKED` finding.
    """
    findings: list[Finding] = []
    pinned: list[Dependency] = []
    for dep in manifest.parse(client.read_manifest(repository, ref)):
        problems = rules.check_fields(dep)
        findings += problems
        if not problems:
            pinned.append(dep)
    try:
        findings += check_pins(client, repository, ref, pinned)
    except (ApiError, ManifestError) as error:
        findings.append(rules.unchecked(str(error)))
    return findings


def check_pins(client: Client, repository: str, ref: str, deps: list[Dependency]) -> list[Finding]:
    """The pin findings against the base manifest: master's manifest at the merge base."""
    comparison = client.compare(repository, BASE_BRANCH, ref)
    files = comparison.get("files") or []
    if len(files) < COMPARE_FILE_LIMIT and all(f.get("filename") != manifest.FILE for f in files):
        return []
    base_text = client.read_manifest(repository, comparison["merge_base_commit"]["sha"])
    base = {dep.name: dep for dep in manifest.parse(base_text)}
    return [f for dep in deps if (f := rules.check_pin(dep, base.get(dep.name), client))]
