"""Read lake-manifest.json at a commit and at the base, and apply the rules in the
action's README.md. Each broken rule is a `Finding`; `__main__` sets its level."""

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
MALFORMED = "malformed"  # The pin is not a full commit hash. An error on every run.
UNMERGEABLE = "unmergeable"  # A PR can test this, but bors must not merge it.
OLDER_PIN = "older-pin"  # The pin is older than the base pin. Also unmergeable.


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
    def allowed_repository(self) -> str | None:
        """The `owner/repo` of `url` when it is in an allowed organization, else `None`."""
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


def lake_update(dep: Dependency) -> str:
    # An inherited dependency comes from the `require` in another dependency, not in Mathlib,
    # so only a full `lake update` changes it.
    return "`lake update`" if dep.inherited else f"`lake update {dep.name}`"


def update_hint(dep: Dependency) -> str:
    return f"Hint: Run {lake_update(dep)} and commit {FILE}."


def require_hint(dep: Dependency) -> str:
    where = "the dependency that requires it" if dep.inherited else "lakefile.lean"
    return f"Hint: Fix the `require` for `{dep.name}` in {where}, then run {lake_update(dep)}."


def check_fields(dep: Dependency) -> list[Finding]:
    """The findings for `inputRev`, `url` and the pin of one dependency."""
    findings = []
    if not (dep.input_rev in BRANCH_REVS or matches(TAG_REV, dep.input_rev)):
        findings.append(Finding(
            UNMERGEABLE,
            "Invalid dependency inputRev",
            f"{FILE} makes `{dep.name}` track `{dep.input_rev}`, which is not an allowed `inputRev`.",
            ("Note: Mathlib can merge only `main`, `master` or a version tag such as `v4.25.0` or `v4.25.0-rc1`.",
             require_hint(dep)),
        ))
    if dep.allowed_repository is None:
        findings.append(Finding(
            UNMERGEABLE,
            "Invalid dependency repository",
            f"{FILE} sets the repository of `{dep.name}` to `{dep.url}`, which is not an allowed repository.",
            ("Note: Mathlib can merge only repositories in https://github.com/leanprover-community "
             "and https://github.com/leanprover.",
             require_hint(dep)),
        ))
    if not matches(FULL_SHA, dep.rev):
        findings.append(Finding(
            MALFORMED,
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
    repository = dep.allowed_repository
    if dep.input_rev not in BRANCH_REVS:
        sha = github.tag_commit(repository, dep.input_rev)
        if sha == dep.rev:
            return None
        return Finding(
            UNMERGEABLE,
            f"Dependency pin does not match tag {dep.input_rev}",
            f"{FILE} pins `{dep.name}` to `{dep.rev}`, but tag `{dep.input_rev}` of {dep.url} "
            + (f"points to `{sha}`." if sha else "does not exist."),
            (update_hint(dep),),
        )
    if not github.is_ancestor(repository, dep.rev, dep.input_rev):
        return Finding(
            UNMERGEABLE,
            f"Dependency pin is not on branch {dep.input_rev}",
            f"{FILE} pins `{dep.name}` to `{dep.rev}`, which is not on the `{dep.input_rev}` branch of {dep.url}.",
            (update_hint(dep),),
        )
    if same_source and not github.is_ancestor(repository, old.rev, dep.rev):
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
    failure in the pin checks is an `UNMERGEABLE` finding.
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
        for dep in valid:
            if finding := check_pin(dep, base.get(dep.name), github):
                findings.append(finding)
    except (ApiError, ValueError) as error:
        findings.append(Finding(
            UNMERGEABLE,
            "Dependency pin check failed",
            f"CI could not check the dependency pins: {error}",
            ("Hint: Re-run the failed job.",),
        ))
    return findings
