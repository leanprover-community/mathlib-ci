"""The rules for the dependencies in a lake-manifest.json, as findings.

`check_fields` needs only the manifest. `check_pin` compares a pin with the
base manifest and asks a `Commits` source about the dependency's history.
The findings carry the problem and the fix. `report` decides their level.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol

from .manifest import Dependency


class Kind(enum.Enum):
    FIELD = "field"  # `inputRev`, `url` or the pin is malformed.
    PIN = "pin"  # The pin is not on its branch or is not its tag's commit.
    OLDER_PIN = "older-pin"  # The pin is older than the base pin.
    UNCHECKED = "unchecked"  # The pin checks could not run.


@dataclass(frozen=True)
class Finding:
    kind: Kind
    title: str
    problem: str
    notes: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()


class Commits(Protocol):
    def is_ancestor(self, owner: str, repo: str, commit: str, tip: str) -> bool:
        """Whether `commit` is `tip` or an ancestor of `tip`. False for an unknown commit."""

    def tag_commit(self, owner: str, repo: str, tag: str) -> str | None:
        """The commit of `tag`, or `None` when the tag does not exist."""


def update_hint(dep: Dependency) -> str:
    return f"Hint: Run `lake update {dep.name}` and commit lake-manifest.json."


def require_hint(dep: Dependency) -> str:
    # An inherited dependency comes from the `require` in another dependency, not in Mathlib.
    if dep.inherited:
        return f"Hint: Fix the `require` for `{dep.name}` in the dependency that requires it, then run `lake update`."
    return f"Hint: Fix the `require` for `{dep.name}` in lakefile.lean, then run `lake update {dep.name}`."


def check_fields(dep: Dependency) -> list[Finding]:
    """The findings for `inputRev`, `url` and the pin of one dependency."""
    findings = []
    if not (dep.tracks_branch or dep.tracks_tag):
        findings.append(Finding(
            Kind.FIELD,
            "Invalid dependency inputRev",
            f"lake-manifest.json sets no `inputRev` for `{dep.name}`." if dep.input_rev is None else
            f"lake-manifest.json makes `{dep.name}` track `{dep.input_rev}`, which is not an allowed `inputRev`.",
            notes=("Note: CI accepts only `main`, `master` or a version tag such as `v4.25.0` or `v4.25.0-rc1`.",),
            hints=(require_hint(dep),),
        ))
    if dep.repository is None:
        findings.append(Finding(
            Kind.FIELD,
            "Invalid dependency repository",
            f"lake-manifest.json sets no repository for `{dep.name}`." if dep.url is None else
            f"lake-manifest.json sets the repository of `{dep.name}` to `{dep.url}`, which is not an allowed repository.",
            notes=("Note: CI accepts only repositories in https://github.com/leanprover-community "
                   "and https://github.com/leanprover.",),
            hints=(require_hint(dep),),
        ))
    if not dep.pins_full_sha:
        findings.append(Finding(
            Kind.FIELD,
            "Invalid dependency pin",
            f"lake-manifest.json pins `{dep.name}` to `{dep.rev}`, which is not a full commit hash.",
            hints=(update_hint(dep),),
        ))
    return findings


def check_pin(dep: Dependency, old: Dependency | None, commits: Commits) -> Finding | None:
    """The finding for the pin of a dependency that passes `check_fields`.

    `old` is the dependency in the base manifest, if any.
    """
    same_source = old is not None and (old.url, old.input_rev) == (dep.url, dep.input_rev)
    if same_source and old.rev == dep.rev:
        return None
    owner, repo = dep.repository
    if dep.tracks_tag:
        sha = commits.tag_commit(owner, repo, dep.input_rev)
        if sha == dep.rev:
            return None
        return Finding(
            Kind.PIN,
            f"Dependency pin does not match tag {dep.input_rev}",
            f"lake-manifest.json pins `{dep.name}` to `{dep.rev}`, but tag `{dep.input_rev}` of {dep.url} "
            + (f"points to `{sha}`." if sha else "does not exist."),
            hints=(update_hint(dep),),
        )
    if not commits.is_ancestor(owner, repo, dep.rev, dep.input_rev):
        return Finding(
            Kind.PIN,
            f"Dependency pin is not on branch {dep.input_rev}",
            f"lake-manifest.json pins `{dep.name}` to `{dep.rev}`, which is not on the `{dep.input_rev}` branch of {dep.url}.",
            hints=(update_hint(dep),),
        )
    if same_source and not commits.is_ancestor(owner, repo, old.rev, dep.rev):
        return Finding(
            Kind.OLDER_PIN,
            "Older dependency pin",
            f"lake-manifest.json pins `{dep.name}` to `{dep.rev}`, which does not descend from `{old.rev}`, the pin on master.",
            hints=(update_hint(dep),),
        )
    return None


def unchecked(reason: str) -> Finding:
    return Finding(
        Kind.UNCHECKED,
        "Dependency pin check failed",
        f"CI could not check the dependency pins: {reason}",
        hints=("Hint: Re-run the failed job.",),
    )
