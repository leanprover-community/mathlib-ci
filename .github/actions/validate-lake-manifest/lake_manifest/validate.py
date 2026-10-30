"""Read lake-manifest.json at a commit and at the base, and apply the rules.

The field rules need only the manifest. The pin rules compare a pin with
the base manifest and ask GitHub about the dependency's history. Each
broken rule is a `Finding`; `__main__` sets its level. The README lists
the rules.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .github import ApiError

FILE = "lake-manifest.json"
BASE_BRANCH = "master"
BRANCH_REVS = frozenset({"main", "master"})
TAG_REV = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]+)?(\+[a-zA-Z0-9.-]+)?")
REPOSITORY_URL = re.compile(r"https://github\.com/((?:leanprover|leanprover-community)/[A-Za-z0-9._-]+)")
FULL_SHA = re.compile(r"[0-9a-f]{40}")

# The kinds of finding.
FIELD = "field"  # `inputRev`, `url` or the pin is malformed.
PIN = "pin"  # The pin is not on its branch, is not its tag's commit, or could not be checked.
OLDER_PIN = "older-pin"  # The pin is older than the base pin.


@dataclass(frozen=True)
class Finding:
    kind: str
    title: str
    problem: str
    advice: tuple[str, ...] = ()  # Notes and hints, one per line.


@dataclass(frozen=True)
class Dependency:
    """One entry of `packages`. A field that is missing or not a string is `None`."""

    name: str | None
    url: str | None
    input_rev: str | None
    rev: str | None
    inherited: bool

    @property
    def repository(self) -> str | None:
        """The `owner/repo` of an allowed GitHub repository, else `None`."""
        match = REPOSITORY_URL.fullmatch(self.url) if self.url is not None else None
        return match[1] if match else None


def matches(pattern: re.Pattern, value: str | None) -> bool:
    return value is not None and pattern.fullmatch(value) is not None


def parse(text: str) -> list[Dependency]:
    """The dependencies of a manifest, in manifest order. Raises `ValueError` for an unusable manifest."""
    manifest = json.loads(text)
    packages = manifest.get("packages") if isinstance(manifest, dict) else None
    if not isinstance(packages, list) or not all(isinstance(p, dict) for p in packages):
        raise ValueError(f"{FILE} has no `packages` list.")

    def text_field(package: dict, key: str) -> str | None:
        value = package.get(key)
        return value if isinstance(value, str) else None

    return [
        Dependency(
            name=text_field(p, "name"),
            url=text_field(p, "url"),
            input_rev=text_field(p, "inputRev"),
            rev=text_field(p, "rev"),
            inherited=p.get("inherited") is True,
        )
        for p in packages
    ]


def update_hint(dep: Dependency) -> str:
    return f"Hint: Run `lake update {dep.name}` and commit {FILE}."


def require_hint(dep: Dependency) -> str:
    # An inherited dependency comes from the `require` in another dependency, not in Mathlib.
    if dep.inherited:
        return f"Hint: Fix the `require` for `{dep.name}` in the dependency that requires it, then run `lake update`."
    return f"Hint: Fix the `require` for `{dep.name}` in lakefile.lean, then run `lake update {dep.name}`."


def check_fields(dep: Dependency) -> list[Finding]:
    """The findings for `inputRev`, `url` and the pin of one dependency."""
    findings = []
    if not (dep.input_rev in BRANCH_REVS or matches(TAG_REV, dep.input_rev)):
        findings.append(Finding(
            FIELD,
            "Invalid dependency inputRev",
            f"{FILE} makes `{dep.name}` track `{dep.input_rev}`, which is not an allowed `inputRev`.",
            ("Note: CI accepts only `main`, `master` or a version tag such as `v4.25.0` or `v4.25.0-rc1`.",
             require_hint(dep)),
        ))
    if dep.repository is None:
        findings.append(Finding(
            FIELD,
            "Invalid dependency repository",
            f"{FILE} sets the repository of `{dep.name}` to `{dep.url}`, which is not an allowed repository.",
            ("Note: CI accepts only repositories in https://github.com/leanprover-community "
             "and https://github.com/leanprover.",
             require_hint(dep)),
        ))
    if not matches(FULL_SHA, dep.rev):
        findings.append(Finding(
            FIELD,
            "Invalid dependency pin",
            f"{FILE} pins `{dep.name}` to `{dep.rev}`, which is not a full commit hash.",
            (update_hint(dep),),
        ))
    return findings


def check_pin(dep: Dependency, old: Dependency | None, github) -> Finding | None:
    """The finding for the pin of a dependency that passes `check_fields`.

    `old` is the dependency in the base manifest, if any.
    """
    same_source = old is not None and (old.url, old.input_rev) == (dep.url, dep.input_rev)
    if same_source and old.rev == dep.rev:
        return None
    if dep.input_rev not in BRANCH_REVS:
        sha = github.tag_commit(dep.repository, dep.input_rev)
        if sha == dep.rev:
            return None
        return Finding(
            PIN,
            f"Dependency pin does not match tag {dep.input_rev}",
            f"{FILE} pins `{dep.name}` to `{dep.rev}`, but tag `{dep.input_rev}` of {dep.url} "
            + (f"points to `{sha}`." if sha else "does not exist."),
            (update_hint(dep),),
        )
    if not github.is_ancestor(dep.repository, dep.rev, dep.input_rev):
        return Finding(
            PIN,
            f"Dependency pin is not on branch {dep.input_rev}",
            f"{FILE} pins `{dep.name}` to `{dep.rev}`, which is not on the `{dep.input_rev}` branch of {dep.url}.",
            (update_hint(dep),),
        )
    if same_source and not github.is_ancestor(dep.repository, old.rev, dep.rev):
        return Finding(
            OLDER_PIN,
            "Older dependency pin",
            f"{FILE} pins `{dep.name}` to `{dep.rev}`, which does not descend from `{old.rev}`, "
            f"the pin on {BASE_BRANCH}.",
            (update_hint(dep),),
        )
    return None


def validate(github, repository: str, ref: str) -> list[Finding]:
    """The findings for lake-manifest.json at `ref`.

    The base manifest is `BASE_BRANCH`'s manifest at the merge base. Raises
    `ApiError` or `ValueError` when the manifest at `ref` cannot be read. A
    failure in the pin checks is a `PIN` finding.
    """
    findings: list[Finding] = []
    valid: list[Dependency] = []
    for dep in parse(github.read_manifest(repository, ref)):
        problems = check_fields(dep)
        findings += problems
        if not problems:
            valid.append(dep)
    try:
        base_ref = github.merge_base(repository, BASE_BRANCH, ref)
        base = {dep.name: dep for dep in parse(github.read_manifest(repository, base_ref))}
        findings += [f for dep in valid if (f := check_pin(dep, base.get(dep.name), github))]
    except (ApiError, ValueError) as error:
        findings.append(Finding(
            PIN,
            "Dependency pin check failed",
            f"CI could not check the dependency pins: {error}",
            ("Hint: Re-run the failed job.",),
        ))
    return findings
